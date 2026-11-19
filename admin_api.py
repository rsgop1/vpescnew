"""Admin REST API for the Vault-Pay web panel."""
import sys
import os

_LIBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_libs")
if os.path.isdir(_LIBS_DIR) and _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from typing import Optional
import json
import logging

log = logging.getLogger(__name__)

from auth import create_token, verify_credentials, require_admin
import db
from db import (
    now_iso, get_treasury, log_action, gen_id,
    get_or_create_user, adjust_balance, get_stats,
)
from config import TELEGRAM_ADMIN_ID, TEST_MODE_ADMIN_ONLY

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _clean(doc):
    if doc is None:
        return None
    if isinstance(doc, list):
        return [_clean(d) for d in doc]
    out = dict(doc)
    return out


# ─── Auth ─────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: dict):
    if not verify_credentials(body.get("username", ""), body.get("password", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(body["username"])
    await log_action(body["username"], "admin_login")
    return {"token": token, "username": body["username"]}


# ─── Dashboard / Stats ────────────────────────────────────────────────────────

@router.get("/stats")
async def stats(_=Depends(require_admin)):
    return await get_stats()


@router.post("/treasury/withdraw")
async def treasury_withdraw(payload: dict, admin=Depends(require_admin)):
    amount = int(payload.get("amount", 0))
    cur = await get_treasury()
    if amount <= 0 or amount > cur:
        raise HTTPException(status_code=400, detail="Invalid amount")
    await db.subtract_from_treasury(amount)
    tx_id = await db.create_transaction({
        "type": "treasury_withdraw", "user_id": 0,
        "amount": amount, "status": "completed",
        "data": {"by": admin},
    })
    await log_action(admin, "treasury_withdraw", {"amount": amount})
    return {"ok": True, "new_balance": cur - amount, "tx_id": tx_id}


# ─── Deals ────────────────────────────────────────────────────────────────────

@router.get("/deals")
async def list_deals(status: Optional[str] = None, _=Depends(require_admin)):
    if status == "all":
        status = None
    return _clean(await db.get_deals(status=status, limit=300))


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: str, _=Depends(require_admin)):
    doc = await db.get_deal(deal_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return _clean(doc)


@router.post("/deals/{deal_id}/force-complete")
async def force_complete(deal_id: str, admin=Depends(require_admin)):
    """Admin force-completes a stuck deal."""
    deal = await db.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Not found")
    if deal.get("status") == "completed":
        raise HTTPException(status_code=400, detail="Already completed")
    await log_action(admin, "force_complete", {"deal_id": deal_id})
    from bot import complete_deal
    class FakeContext:
        class bot:
            @staticmethod
            async def send_message(*a, **kw): pass
            @staticmethod
            async def edit_message_text(*a, **kw): pass
            @staticmethod
            async def pin_chat_message(*a, **kw): pass
    await complete_deal(FakeContext(), deal_id)
    return {"ok": True}


# ─── Disputes ─────────────────────────────────────────────────────────────────

@router.get("/disputes")
async def list_disputes(_=Depends(require_admin)):
    disputes = await db.get_disputes()
    result = []
    for d in disputes:
        deal = await db.get_deal(d["deal_id"])
        d["deal"] = _clean(deal)
        try:
            transcript = json.loads((deal or {}).get("room_transcript_json") or "[]")
        except Exception:
            transcript = []
        d["transcript_count"] = len(transcript)
        d["room_locked"] = bool((deal or {}).get("room_chat_locked"))
        d["case_link"] = f"/disputes?deal={d['deal_id']}"
        result.append(_clean(d))
    return result


@router.get("/disputes/{deal_id}")
async def get_dispute_case(deal_id: str, _=Depends(require_admin)):
    """Return a complete admin case: issue evidence, room transcript and deal state."""
    deal = await db.get_deal(deal_id)
    dispute = await db.get_dispute(deal_id)
    if not deal or not dispute:
        raise HTTPException(status_code=404, detail="Dispute case not found")
    try:
        transcript = json.loads(deal.get("room_transcript_json") or "[]")
    except Exception:
        transcript = []
    return _clean({
        **dispute,
        "deal": deal,
        "room_transcript": transcript,
        "case_link": f"/disputes?deal={deal_id}",
    })


async def execute_dispute_resolution(deal_id: str, action: str, resolved_by: str, note: str = ""):
    """Apply a manual resolution, notify both parties, and refresh the deal card."""
    deal = await db.get_deal(deal_id)
    if not deal:
        raise ValueError("Deal not found")

    dispute = await db.get_dispute(deal_id)
    if dispute and dispute.get("status") == "resolved":
        raise ValueError("Dispute already resolved")

    if action not in ("release_seller", "refund_buyer", "split"):
        raise ValueError(f"Invalid action: {action}")

    is_manual_middleman = deal.get("payment_method") == "middleman" or deal.get("room_chat_id")
    if is_manual_middleman:
        # The bot must not invent wallet/VC movements for an off-platform UPI/cash
        # settlement. This records the admin decision and leaves settlement evidence
        # to the manual operator.
        next_status = "cancelled" if action == "refund_buyer" else "completed"
        fields = {"status": next_status}
        if action == "release_seller":
            fields["released_at"] = now_iso()
        await db.update_deal(deal_id, **fields)
    else:
        locked = deal.get("locked_amount") or deal.get("amount", 0)
        fee_pct = deal.get("fee_percent", 1.0)
        from bot import compute_fee
        buyer_debit, seller_credit, fee_amt = compute_fee(deal["amount"], fee_pct, deal.get("fee_payer", "buyer"))

        if action == "release_seller":
            if deal.get("buyer_id"):
                await adjust_balance(deal["buyer_id"], delta_locked=-locked)
            if deal.get("seller_id"):
                await adjust_balance(deal["seller_id"], delta_balance=seller_credit)
            await db.add_to_treasury(fee_amt)
            await db.update_deal(deal_id, status="completed", released_at=now_iso())

        elif action == "refund_buyer":
            if deal.get("buyer_id"):
                await adjust_balance(deal["buyer_id"], delta_balance=locked, delta_locked=-locked)
            await db.update_deal(deal_id, status="cancelled")

        elif action == "split":
            half = locked // 2
            if deal.get("buyer_id"):
                await adjust_balance(deal["buyer_id"], delta_balance=half, delta_locked=-locked)
            if deal.get("seller_id"):
                await adjust_balance(deal["seller_id"], delta_balance=locked - half)
            await db.update_deal(deal_id, status="completed")

    await db.update_dispute(
        deal_id, status="resolved", resolution=action, resolved_by=resolved_by,
        resolved_at=now_iso(), resolution_note=(note or "")[:4000],
    )
    await log_action(resolved_by, "dispute_resolved", {"deal_id": deal_id, "action": action})

    await _send_resolution_report(deal_id, action, resolved_by)


async def _send_resolution_report(deal_id: str, action: str, resolved_by: str):
    """Tell buyer, seller, and admin exactly what manual decision was applied."""
    from bot import send_dm, _bot_app, build_deal_card, esc as bot_esc

    deal = await db.get_deal(deal_id)
    dispute = await db.get_dispute(deal_id)
    if not deal:
        return

    manual = deal.get("payment_method") == "middleman" or deal.get("room_chat_id")
    label_map = {
        "release_seller": "seller-favouring decision recorded",
        "refund_buyer": "buyer refund decision recorded",
        "split": "50/50 settlement decision recorded",
    }
    detail = label_map.get(action, action)
    lines = [
        f"⚖️ *Manual Dispute Resolution — Deal \\#{bot_esc(deal_id)}*",
        f"Decision: *{bot_esc(detail)}*",
        f"Decided by: {bot_esc(str(resolved_by))}",
    ]
    if manual:
        lines.append("Settlement note: This was an off-platform/manual case. Admin settlement must be handled and evidenced separately.")
    if dispute and dispute.get("resolution_note"):
        lines.append(f"Admin note: {bot_esc(dispute['resolution_note'][:800])}")
    report = "\n".join(lines)

    for uid in [deal.get("buyer_id"), deal.get("seller_id"), TELEGRAM_ADMIN_ID]:
        if uid:
            await send_dm(uid, report)

    if deal.get("chat_id") and deal.get("message_id") and _bot_app:
        try:
            card_text, card_markup = build_deal_card(deal)
            await _bot_app.bot.edit_message_text(
                card_text, chat_id=deal["chat_id"], message_id=deal["message_id"],
                parse_mode="MarkdownV2", reply_markup=card_markup,
                disable_web_page_preview=True,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"resolution group card edit failed: {e}")


@router.post("/disputes/{deal_id}/resolve")
async def resolve_dispute(deal_id: str, payload: dict, admin=Depends(require_admin)):
    action = payload.get("action")
    note = (payload.get("note") or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Resolution note is required")
    try:
        await execute_dispute_resolution(deal_id, action, resolved_by=admin, note=note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/disputes/{deal_id}/ping-seller")
async def ping_seller(deal_id: str, admin=Depends(require_admin)):
    deal = await db.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Not found")
    from bot import send_dm
    if deal.get("seller_id"):
        await send_dm(
            deal["seller_id"],
            f"⚠️ Admin has summoned you for Deal #{deal_id}. Provide your evidence in DM."
        )
    return {"ok": True}


# ─── Financial Queue ──────────────────────────────────────────────────────────

@router.get("/deposits/pending")
async def pending_deposits(_=Depends(require_admin)):
    txs = await db.get_transactions(tx_type="deposit", status="pending")
    result = []
    for t in txs:
        try:
            t["data"] = json.loads(t.get("data_json", "{}"))
        except Exception:
            t["data"] = {}
        result.append(_clean(t))
    return result


@router.get("/deposits/{tx_id}/screenshot")
async def get_deposit_screenshot(tx_id: str, _=Depends(require_admin)):
    """Return the Telegram file_id of a deposit screenshot so admin can view it."""
    tx = await db.get_transaction(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        data = json.loads(tx.get("data_json", "{}"))
    except Exception:
        data = {}
    return {
        "photo_file_id": data.get("photo_file_id", ""),
        "proof_text": data.get("proof_text", ""),
        "username": data.get("username", ""),
        "tg_id": data.get("tg_id", tx.get("user_id")),
        "amount": tx.get("amount", 0),
        "status": tx.get("status"),
    }


@router.get("/telegram-photo/{file_id}")
async def get_telegram_photo(file_id: str, _=Depends(require_admin)):
    """Stream the actual image bytes for a Telegram file_id (evidence photo, deposit
    screenshot, vouch proof, etc.) so the admin dashboard can render a real <img>, not just a
    file-id string. Admin-gated like everything else here."""
    try:
        import bot as bot_module
        if not bot_module._bot_app:
            raise HTTPException(status_code=503, detail="Bot not ready yet")
        tg_file = await bot_module._bot_app.bot.get_file(file_id)
        raw = await tg_file.download_as_bytearray()
        if not raw:
            raise HTTPException(status_code=404, detail="Empty file from Telegram")
        return Response(content=bytes(raw), media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"telegram-photo fetch failed for {file_id}: {e}")
        raise HTTPException(status_code=404, detail="Could not fetch photo")


@router.post("/deposits/{tx_id}/approve")
async def approve_deposit(tx_id: str, admin=Depends(require_admin)):
    tx = await db.get_transaction(tx_id)
    if not tx or tx.get("status") == "approved":
        raise HTTPException(status_code=404, detail="Not found or already approved")
    await adjust_balance(tx["user_id"], delta_balance=tx["amount"])
    # Store approval info back into data_json
    try:
        data = json.loads(tx.get("data_json", "{}"))
    except Exception:
        data = {}
    data["approved_by"] = admin
    data["approved_at"] = now_iso()
    await db.update_transaction(tx_id, status="approved", data_json=json.dumps(data))
    await log_action(admin, "deposit_approved", {"user": tx["user_id"], "amount": tx["amount"]})
    from bot import send_dm
    await send_dm(
        tx["user_id"],
        f"✅ *Deposit Approved\\!*\n"
        f"*{tx['amount']:,} VC* has been credited to your wallet\\. /wallet to check\\.",
        parse_mode="MarkdownV2",
    )
    return {"ok": True, "credited": tx["amount"]}


@router.post("/deposits/{tx_id}/reject")
async def reject_deposit(tx_id: str, payload: dict = {}, admin=Depends(require_admin)):
    tx = await db.get_transaction(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Not found")
    reason = payload.get("reason", "Payment could not be verified") if isinstance(payload, dict) else "Rejected"
    try:
        data = json.loads(tx.get("data_json", "{}"))
    except Exception:
        data = {}
    data["rejected_by"] = admin
    data["rejected_at"] = now_iso()
    data["reject_reason"] = reason
    await db.update_transaction(tx_id, status="rejected", data_json=json.dumps(data))
    from bot import send_dm
    await send_dm(
        tx["user_id"],
        f"❌ *Deposit Rejected*\n"
        f"Amount: {tx['amount']:,} VC\n"
        f"Reason: {reason}\n\n"
        f"Contact admin if you believe this is incorrect\\.",
        parse_mode="MarkdownV2",
    )
    return {"ok": True}


@router.get("/withdrawals/pending")
async def pending_withdrawals(_=Depends(require_admin)):
    txs = await db.get_transactions(tx_type="withdrawal", status="pending")
    result = []
    for t in txs:
        try:
            t["data"] = json.loads(t.get("data_json", "{}"))
        except Exception:
            t["data"] = {}
        result.append(_clean(t))
    return result


@router.post("/withdrawals/{tx_id}/paid")
async def withdrawal_paid(tx_id: str, admin=Depends(require_admin)):
    tx = await db.get_transaction(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        data = json.loads(tx.get("data_json", "{}"))
    except Exception:
        data = {}
    data["paid_by"] = admin
    data["paid_at"] = now_iso()
    await db.update_transaction(tx_id, status="paid", data_json=json.dumps(data))
    await log_action(admin, "withdrawal_paid", {"user": tx["user_id"], "amount": tx["amount"]})
    from bot import send_dm
    await send_dm(
        tx["user_id"],
        f"✅ *Withdrawal Successful\\!*\n"
        f"*{tx['amount']:,} VC* has been paid to your UPI\\. 🏦\n"
        f"Thank you for using Vault\\-Pay\\!",
        parse_mode="MarkdownV2",
    )
    return {"ok": True}


@router.post("/withdrawals/{tx_id}/reject")
async def reject_withdrawal(tx_id: str, payload: dict = {}, admin=Depends(require_admin)):
    tx = await db.get_transaction(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Not found")
    if tx.get("status") == "rejected":
        raise HTTPException(status_code=400, detail="Already rejected")
    reason = (payload or {}).get("reason", "Withdrawal could not be processed")
    # Refund balance
    await adjust_balance(tx["user_id"], delta_balance=tx["amount"])
    try:
        data = json.loads(tx.get("data_json", "{}"))
    except Exception:
        data = {}
    data["rejected_by"] = admin
    data["rejected_at"] = now_iso()
    data["reject_reason"] = reason
    await db.update_transaction(tx_id, status="rejected", data_json=json.dumps(data))
    await log_action(admin, "withdrawal_rejected", {"user": tx["user_id"], "amount": tx["amount"], "reason": reason})
    from bot import send_dm
    await send_dm(
        tx["user_id"],
        f"❌ *Withdrawal Rejected & Refunded*\n"
        f"Amount: *{tx['amount']:,} VC*\n"
        f"Reason: {reason}\n\n"
        f"Your *{tx['amount']:,} VC* has been returned to your wallet\\.",
        parse_mode="MarkdownV2",
    )
    return {"ok": True}


# ─── Users / Guillotine ───────────────────────────────────────────────────────

@router.get("/users")
async def list_users(_=Depends(require_admin)):
    users = await db.get_all_users(limit=500)
    return _clean(users)


@router.post("/users/{tg_id}/freeze")
async def freeze_user(tg_id: int, admin=Depends(require_admin)):
    await db.update_user(tg_id, frozen=True)
    await log_action(admin, "user_frozen", {"tg_id": tg_id})
    return {"ok": True}


@router.post("/users/{tg_id}/unfreeze")
async def unfreeze_user(tg_id: int, admin=Depends(require_admin)):
    await db.update_user(tg_id, frozen=False)
    await log_action(admin, "user_unfrozen", {"tg_id": tg_id})
    return {"ok": True}


@router.post("/users/{tg_id}/flag-scam")
async def flag_scam(tg_id: int, admin=Depends(require_admin)):
    await db.update_user(tg_id, scam_flag=True)
    await log_action(admin, "scam_flagged", {"tg_id": tg_id})
    return {"ok": True}


@router.post("/users/{tg_id}/unflag-scam")
async def unflag_scam(tg_id: int, admin=Depends(require_admin)):
    await db.update_user(tg_id, scam_flag=False)
    await log_action(admin, "scam_unflagged", {"tg_id": tg_id})
    return {"ok": True}


@router.post("/users/{tg_id}/wipe-rep")
async def wipe_rep(tg_id: int, admin=Depends(require_admin)):
    await db.update_user(tg_id, rating_sum=0, rating_count=0, vouches=0, badges='[]')
    await log_action(admin, "rep_wiped", {"tg_id": tg_id})
    return {"ok": True}


@router.post("/users/{tg_id}/adjust-balance")
async def adjust_user_balance(tg_id: int, payload: dict, admin=Depends(require_admin)):
    amount = int(payload.get("amount", 0))
    reason = payload.get("reason", "Admin adjustment")
    await adjust_balance(tg_id, delta_balance=amount)
    await log_action(admin, "balance_adjusted", {"tg_id": tg_id, "amount": amount, "reason": reason})
    from bot import send_dm
    sign = "+" if amount >= 0 else ""
    await send_dm(tg_id, f"💰 Admin adjustment: {sign}{amount:,} VC. Reason: {reason}")
    return {"ok": True}


@router.post("/users/bulk-freeze")
async def bulk_freeze(payload: dict, admin=Depends(require_admin)):
    tg_ids = payload.get("tg_ids", [])
    for tg_id in tg_ids:
        await db.update_user(int(tg_id), frozen=True)
    await log_action(admin, "bulk_frozen", {"count": len(tg_ids)})
    return {"ok": True, "count": len(tg_ids)}


@router.get("/scam-reports")
async def get_scam_reports(_=Depends(require_admin)):
    return _clean(await db.get_scam_reports())


# ─── Blacklist ────────────────────────────────────────────────────────────────

@router.get("/blacklist")
async def get_blacklist_list(_=Depends(require_admin)):
    return _clean(await db.get_blacklist())


@router.post("/blacklist")
async def add_to_blacklist(payload: dict, admin=Depends(require_admin)):
    value = payload.get("value", "").strip()
    bl_type = payload.get("type", "other")
    reason = payload.get("reason", "")
    if not value:
        raise HTTPException(status_code=400, detail="Value required")
    await db.add_blacklist(value, bl_type, reason, admin)
    await log_action(admin, "blacklisted", {"value": value, "type": bl_type})
    return {"ok": True}


@router.delete("/blacklist/{value}")
async def remove_from_blacklist(value: str, admin=Depends(require_admin)):
    await db.del_blacklist(value)
    await log_action(admin, "blacklist_removed", {"value": value})
    return {"ok": True}


# ─── Groups ───────────────────────────────────────────────────────────────────

@router.get("/groups")
async def list_groups(_=Depends(require_admin)):
    return _clean(await db.get_all_groups())


@router.post("/groups")
async def add_group(payload: dict, admin=Depends(require_admin)):
    chat_id = int(payload.get("chat_id", 0))
    title = payload.get("title", "")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id required")
    await db.add_group(chat_id, title, admin)
    await log_action(admin, "group_added", {"chat_id": chat_id})
    return {"ok": True}


@router.delete("/groups/{chat_id}")
async def del_group(chat_id: int, admin=Depends(require_admin)):
    await db.remove_group(chat_id)
    await log_action(admin, "group_removed", {"chat_id": chat_id})
    return {"ok": True}


# ─── Settings ─────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_all_settings(_=Depends(require_admin)):
    fee = await db.get_setting("fee_percent", 1.0)
    maint = await db.get_setting("maintenance_mode", False)
    test_mode_admin_only = await db.get_setting("test_mode_admin_only", TEST_MODE_ADMIN_ONLY)
    fee_holiday = await db.get_setting("fee_holiday_until", "")
    default_toc = await db.get_setting("default_toc", "")
    return {
        "fee_percent": fee,
        "maintenance_mode": maint,
        "test_mode_admin_only": test_mode_admin_only,
        "fee_holiday_until": fee_holiday,
        "default_toc": default_toc,
    }


@router.post("/settings")
async def update_settings(payload: dict, admin=Depends(require_admin)):
    if "fee_percent" in payload:
        v = float(payload["fee_percent"])
        if v < 0 or v > 10:
            raise HTTPException(status_code=400, detail="Fee must be 0-10%")
        await db.set_setting("fee_percent", v)
    if "maintenance_mode" in payload:
        await db.set_setting("maintenance_mode", bool(payload["maintenance_mode"]))
    if "test_mode_admin_only" in payload:
        await db.set_setting("test_mode_admin_only", bool(payload["test_mode_admin_only"]))
    if "fee_holiday_until" in payload:
        await db.set_setting("fee_holiday_until", payload["fee_holiday_until"])
    if "default_toc" in payload:
        await db.set_setting("default_toc", str(payload["default_toc"]))
    await log_action(admin, "settings_updated", payload)
    return {"ok": True}


# ─── Broadcast ────────────────────────────────────────────────────────────────

@router.post("/broadcast")
async def broadcast(payload: dict, admin=Depends(require_admin)):
    text = payload.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    from bot import broadcast_message
    count = await broadcast_message(text)
    tx_id = await db.create_transaction({
        "type": "broadcast", "user_id": 0,
        "amount": 0, "status": "sent",
        "data": {"text": text[:200], "by": admin, "count": count},
    })
    await log_action(admin, "broadcast", {"chars": len(text), "count": count})
    return {"ok": True, "sent": count}


@router.get("/broadcast-templates")
async def get_broadcast_templates(_=Depends(require_admin)):
    return _clean(await db.get_broadcast_templates())


@router.post("/broadcast-templates")
async def add_broadcast_template(payload: dict, admin=Depends(require_admin)):
    name = payload.get("name", "").strip()
    text = payload.get("text", "").strip()
    if not name or not text:
        raise HTTPException(status_code=400, detail="Name and text required")
    await db.add_broadcast_template(name, text, admin)
    return {"ok": True}


@router.delete("/broadcast-templates/{tmpl_id}")
async def del_broadcast_template(tmpl_id: str, _=Depends(require_admin)):
    await db.del_broadcast_template(tmpl_id)
    return {"ok": True}


# ─── Audit log ────────────────────────────────────────────────────────────────

@router.get("/audit")
async def get_audit_log(_=Depends(require_admin)):
    return _clean(await db.get_audit_log(limit=300))


# ─── Templates ────────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates(_=Depends(require_admin)):
    return _clean(await db.get_templates())


@router.post("/templates")
async def add_template(payload: dict, admin=Depends(require_admin)):
    name = payload.get("name", "").strip()
    toc = payload.get("toc", "").strip()
    amount = int(payload.get("amount", 0))
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    tmpl_id = await db.add_template(name, toc, amount, admin)
    return {"ok": True, "id": tmpl_id}


@router.delete("/templates/{tmpl_id}")
async def delete_template(tmpl_id: str, _=Depends(require_admin)):
    await db.del_template(tmpl_id)
    return {"ok": True}


# ─── Middlemen ────────────────────────────────────────────────────────────────

@router.get("/middlemen")
async def list_middlemen(_=Depends(require_admin)):
    return _clean(await db.get_all_middlemen())


@router.post("/middlemen")
async def add_middleman_ep(payload: dict, admin=Depends(require_admin)):
    tg_id = payload.get("tg_id")
    username = payload.get("username", "").strip().lstrip("@")
    if not tg_id:
        raise HTTPException(status_code=400, detail="tg_id required")
    try:
        tg_id = int(tg_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="tg_id must be a number")
    await db.add_middleman(tg_id, username, admin)
    await log_action(admin, "middleman_added", {"tg_id": tg_id, "username": username})
    return {"ok": True}


@router.post("/middlemen/{tg_id}/toggle")
async def toggle_middleman_ep(tg_id: int, payload: dict, admin=Depends(require_admin)):
    active = bool(payload.get("active", True))
    await db.toggle_middleman(tg_id, active)
    await log_action(admin, "middleman_toggled", {"tg_id": tg_id, "active": active})
    return {"ok": True}


@router.delete("/middlemen/{tg_id}")
async def delete_middleman_ep(tg_id: int, admin=Depends(require_admin)):
    await db.delete_middleman(tg_id)
    await log_action(admin, "middleman_removed", {"tg_id": tg_id})
    return {"ok": True}


# ─── Reusable middleman rooms ─────────────────────────────────────────────────

@router.get("/middleman-rooms")
async def list_middleman_rooms(_=Depends(require_admin)):
    rooms = await db.get_middleman_rooms()
    result = []
    for room in rooms:
        item = _clean(room)
        item["active_deal"] = _clean(await db.get_deal(room["deal_id"])) if room.get("deal_id") else None
        result.append(item)
    return result


@router.post("/middleman-rooms/{chat_id}/release")
async def release_middleman_room(chat_id: int, admin=Depends(require_admin)):
    room = await db.get_middleman_room(chat_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    deal = await db.get_deal(room["deal_id"]) if room.get("deal_id") else None
    if deal and deal.get("status") == "disputed":
        raise HTTPException(
            status_code=409,
            detail="Disputed room is frozen. Resolve the case before cleanup.",
        )
    try:
        from bot import _bot_app, cleanup_middleman_room
        if deal and _bot_app:
            await cleanup_middleman_room(_bot_app, deal)
        else:
            await db.set_middleman_room_status(chat_id, "available", "")
    except Exception as e:
        log.warning(f"admin room release failed for {chat_id}: {e}")
        await db.set_middleman_room_status(chat_id, "quarantined", room.get("deal_id") or "")
        raise HTTPException(status_code=500, detail="Cleanup failed; room was quarantined")
    await log_action(admin, "middleman_room_released", {"chat_id": chat_id, "deal_id": room.get("deal_id")})
    return {"ok": True}


@router.post("/middleman-rooms/{chat_id}/clean")
async def clean_quarantined_middleman_room(chat_id: int, payload: dict | None = None, admin=Depends(require_admin)):
    """Explicit admin recovery path for a quarantined room.

    A disputed room requires an explicit confirmation because Telegram messages
    are removed from the room after the transcript has been preserved in DB.
    """
    room = await db.get_middleman_room(chat_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    deal = await db.get_deal(room["deal_id"]) if room.get("deal_id") else None
    is_disputed = bool(deal and deal.get("status") == "disputed")
    acknowledged = bool((payload or {}).get("acknowledge_dispute_cleanup"))
    if is_disputed and not acknowledged:
        raise HTTPException(
            status_code=409,
            detail="This room contains a disputed case. Confirm transcript-preserved cleanup first.",
        )
    try:
        from bot import _bot_app, cleanup_middleman_room
        if deal and _bot_app:
            ok = await cleanup_middleman_room(_bot_app, deal)
            if not ok:
                raise RuntimeError("Telegram cleanup was incomplete")
        else:
            await db.set_middleman_room_status(chat_id, "available", "")
    except Exception as e:
        log.warning(f"admin quarantined room cleanup failed for {chat_id}: {e}")
        await db.set_middleman_room_status(chat_id, "quarantined", room.get("deal_id") or "")
        raise HTTPException(status_code=500, detail="Cleanup failed; room remains quarantined")
    await log_action(admin, "middleman_room_force_cleaned", {
        "chat_id": chat_id,
        "deal_id": room.get("deal_id"),
        "disputed": is_disputed,
    })
    return {"ok": True, "transcript_preserved": is_disputed}


@router.post("/middleman-rooms/{chat_id}/quarantine")
async def quarantine_middleman_room(chat_id: int, payload: dict | None = None, admin=Depends(require_admin)):
    room = await db.get_middleman_room(chat_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    await db.set_middleman_room_status(chat_id, "quarantined", room.get("deal_id") or "")
    await log_action(admin, "middleman_room_quarantined", {
        "chat_id": chat_id,
        "deal_id": room.get("deal_id"),
        "reason": (payload or {}).get("reason", ""),
    })
    return {"ok": True}


# ─── Vouches ──────────────────────────────────────────────────────────────────

@router.get("/vouches/pending")
async def get_pending_vouches_ep(_=Depends(require_admin)):
    vouches = await db.get_pending_vouches()
    return _clean(vouches)


@router.post("/vouches/{vouch_id}/approve")
async def approve_vouch_ep(vouch_id: str, admin=Depends(require_admin)):
    v = await db.approve_vouch(vouch_id)
    if not v:
        raise HTTPException(status_code=404, detail="Vouch not found or already processed")
    await log_action(admin, "vouch_approved", {"vouch_id": vouch_id})
    from bot import send_dm
    if v.get("to_id"):
        await send_dm(
            v["to_id"],
            f"🎉 *Vouch Approved\\!*\n"
            f"Verified vouch from @{v.get('from_un') or str(v.get('from_id','?'))} approved\\!\n"
            f"Your trust profile has been updated\\.",
            parse_mode="MarkdownV2",
        )
    if v.get("from_id"):
        await send_dm(
            v["from_id"],
            f"✅ *Your vouch for @{v.get('to_un') or str(v.get('to_id','?'))} was approved\\!*",
            parse_mode="MarkdownV2",
        )
    return {"ok": True}


@router.post("/vouches/{vouch_id}/reject")
async def reject_vouch_ep(vouch_id: str, payload: dict = {}, admin=Depends(require_admin)):
    v = await db.reject_vouch(vouch_id)
    if not v:
        raise HTTPException(status_code=404, detail="Vouch not found or already processed")
    reason = (payload or {}).get("reason", "Vouch could not be verified")
    await log_action(admin, "vouch_rejected", {"vouch_id": vouch_id, "reason": reason})
    from bot import send_dm
    if v.get("from_id"):
        await send_dm(
            v["from_id"],
            f"❌ *Vouch Rejected*\n"
            f"Reason: {reason}\n\n"
            f"Submit stronger proof with +vouch if you believe it's valid\\.",
            parse_mode="MarkdownV2",
        )
    return {"ok": True}


# ─── Export ───────────────────────────────────────────────────────────────────

@router.get("/export/{collection}")
async def export_data(collection: str, _=Depends(require_admin)):
    valid = {"users", "deals", "transactions", "disputes", "blacklist", "groups_tbl", "audit_log", "vouches", "referrals", "deal_templates"}
    if collection not in valid:
        raise HTTPException(status_code=400, detail="Invalid collection")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT * FROM {collection} LIMIT 10000")
    return {"data": [dict(r) for r in rows], "count": len(rows), "collection": collection}
