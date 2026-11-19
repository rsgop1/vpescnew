"""Vault-Pay Escrow Bot — v3 Complete Rewrite (bug-free, clean flow)"""
import sys
import os

_LIBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_libs")
if os.path.isdir(_LIBS_DIR) and _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

import asyncio, hashlib, io, json, logging, random, re
from datetime import datetime, timezone, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
    ChatPermissions,
)
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ChatJoinRequestHandler, filters,
)
from telegram.error import BadRequest, Forbidden

import db
import auto_ltc
from transcript_pdf import build_transcript_pdf
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID, TELEGRAM_CHANNEL_ID,
    TELEGRAM_ADMIN_ID, ADMIN_UPI_ID, DEFAULT_FEE_PERCENT,
    RISK_CHECK_AMOUNT_THRESHOLD, ABUSE_WARNING_LIMIT, TEST_MODE_ADMIN_ONLY,
)

log = logging.getLogger(__name__)

# ─── In-memory state ──────────────────────────────────────────────────────────
DRAFT: dict[int, dict] = {}           # user_id → deal creation draft
WALLET_STATE: dict[int, dict] = {}    # user_id → wallet flow state
PIN_AWAIT: dict[int, str] = {}         # user_id → deal_id (awaiting PIN)
DISPUTE_AWAIT: dict[int, str] = {}     # user_id → deal_id (awaiting dispute text)
RELAY: dict[int, dict] = {}            # user_id → {deal_id, other_id, role}
MM_STATE: dict = {}                    # deal_id → middleman deal state dict
MM_DM_AWAIT: dict[int, dict] = {}     # user_id → {role, deal_id, step}
CUSTOM_TOC_AWAIT: dict[int, bool] = {}  # user_id → True (waiting for custom ToS text in group)
VOUCH_PROOF_AWAIT: dict[int, dict] = {} # user_id → {vouch_id, target_id, target_un, proofs}
EVIDENCE_AWAIT: dict[int, dict] = {}   # user_id → {deal_id, count} (dispute evidence collection)
NUDGED: set = set()                     # deal_ids already nudged this session
DISPUTE_DONE: dict[str, set] = {}       # deal_id → set of user_ids who typed "done" on evidence

_bot_app: Application | None = None
_bot_username: str = ""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def kb(rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows]
    )


def esc(text) -> str:
    """Escape text for MarkdownV2."""
    if text is None:
        return ""
    chars = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in chars else c for c in str(text))


def escn(n, fmt=None) -> str:
    """Format a number and escape for MarkdownV2 (handles decimal dots)."""
    s = f"{n:{fmt}}" if fmt else str(n)
    return esc(s)


async def safe_edit(msg, text: str, markup=None):
    """Edit a message, swallowing benign errors."""
    try:
        await msg.edit_text(
            text, parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=markup, disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning(f"safe_edit: {e}")
    except Exception as e:
        log.warning(f"safe_edit: {e}")


DEAL_STATUS_LABELS = {
    "awaiting_payment":    "⏳ Awaiting Middleman Connection",
    "waiting_buyer_pay":   "💳 Awaiting Buyer Payment to Middleman",
    "in_progress":         "🔄 In Progress",
    "buyer_paid":          "💳 Buyer Paid — Awaiting Middleman Confirmation",
    "mm_connecting":       "🔄 Participants Joining Private Room",
    "mm_payment_received": "✅ Payment Received by Middleman",
    "mm_asset_released":   "🔓 Asset Released — Seller Payment in Progress",
    "mm_seller_paid":      "💸 Seller Payment Sent — Awaiting Confirmation",
    "completed":           "🎉 Completed Successfully",
    "disputed":            "⚠️ Under Dispute",
    "cancelled":           "❌ Cancelled",
    "expired":             "💨 Expired (2h Timeout)",
}


def format_time_taken(deal: dict) -> str:
    """Calculate elapsed or total duration for a deal."""
    created = deal.get("created_at")
    if not created:
        return "N/A"
    try:
        c_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        end_ts = deal.get("released_at") or deal.get("cancelled_at")
        if deal.get("status") in ("completed", "cancelled", "expired") and end_ts:
            e_dt = datetime.fromisoformat(str(end_ts).replace("Z", "+00:00"))
        else:
            e_dt = datetime.now(timezone.utc)
        secs = max(0, int((e_dt - c_dt).total_seconds()))
        hrs, rem = divmod(secs, 3600)
        mins, s = divmod(rem, 60)
        if hrs > 0:
            return f"{hrs}h {mins}m {s}s"
        elif mins > 0:
            return f"{mins}m {s}s"
        else:
            return f"{s}s"
    except Exception:
        return "N/A"


def format_dt(dt_val: str | None) -> str:
    """Format an ISO timestamp for display."""
    if not dt_val:
        return "N/A"
    try:
        dt = datetime.fromisoformat(str(dt_val).replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return str(dt_val)[:19]


def plain_deal_card(deal: dict) -> str:
    """Fallback plain-text group card without Markdown parsing."""
    status = deal.get("status", "awaiting_payment")
    amount = deal.get("amount", 0)
    fee_pct = deal.get("fee_percent", 1.0)
    fee_payer = str(deal.get("fee_payer") or "buyer").lower()
    _, _, fee_amt = compute_fee(amount, fee_pct, fee_payer)

    deal_id = str(deal.get("deal_id", ""))
    product_name = str(deal.get("product_name") or "Standard Escrow")[:20]
    tos = str(deal.get("toc") or "Standard Escrow Protection Terms")[:50]

    buyer_un = str(deal.get("buyer_un") or deal.get("buyer_id") or "Buyer")
    seller_un = str(deal.get("seller_un") or deal.get("seller_id") or "Seller")
    middleman_un = str(
        deal.get("middleman_un")
        or ("Assigned" if deal.get("middleman_id") else "Pending Assignment")
    )

    status_label = DEAL_STATUS_LABELS.get(status, status.replace("_", " ").title())
    time_taken = format_time_taken(deal)
    created_str = format_dt(deal.get("created_at"))

    out = (
        f"VAULT-PAY ESCROW DEAL #{deal_id}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Product: {product_name}\n"
        f"Amount: ₹{amount:,}\n"
        f"ToS: {tos}\n"
        f"Fee (1%): ₹{fee_amt:,} (Paid by: {fee_payer.title()})\n"
        f"Buyer: @{buyer_un}\n"
        f"Seller: @{seller_un}\n"
        f"Middleman: @{middleman_un}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: {status_label}\n"
        f"Time Taken: {time_taken}\n"
        f"Created: {created_str}\n"
    )
    if deal.get("released_at"):
        out += f"Completed: {format_dt(deal.get('released_at'))}\n"
    elif deal.get("cancelled_at"):
        out += f"Cancelled: {format_dt(deal.get('cancelled_at'))}\n"
    out += "\nNote: Public group status card (read-only). All action buttons are located inside the registered private deal room."
    return out


async def publish_group_deal_card(context, deal: dict, pin: bool = True) -> bool:
    """Edit, repair, and pin the canonical deal card in the original group."""
    deal_id = deal.get("deal_id")
    if deal_id:
        db_deal = await db.get_deal(deal_id)
        if db_deal:
            deal = {**dict(db_deal), **deal}
    chat_id = deal.get("chat_id")
    message_id = deal.get("message_id")
    if not chat_id:
        return False
    card_text, card_markup = build_deal_card(deal)
    current_id = message_id
    try:
        if current_id:
            await context.bot.edit_message_text(
                card_text, chat_id=chat_id, message_id=current_id,
                parse_mode=ParseMode.MARKDOWN_V2, reply_markup=card_markup,
                disable_web_page_preview=True,
            )
        else:
            sent = await context.bot.send_message(
                chat_id, card_text, parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=card_markup, disable_web_page_preview=True,
            )
            current_id = sent.message_id
    except Exception as first_error:
        log.warning(f"group deal card markdown failed for {deal.get('deal_id')}: {first_error}")
        try:
            if current_id:
                await context.bot.edit_message_text(
                    plain_deal_card(deal), chat_id=chat_id, message_id=current_id,
                    parse_mode=None, reply_markup=card_markup,
                )
            else:
                sent = await context.bot.send_message(
                    chat_id, plain_deal_card(deal), parse_mode=None, reply_markup=card_markup,
                )
                current_id = sent.message_id
        except Exception as second_error:
            log.error(f"group deal card fallback failed for {deal.get('deal_id')}: {second_error}")
            return False

    if current_id and current_id != message_id:
        await db.update_deal(deal["deal_id"], message_id=current_id)
        deal["message_id"] = current_id
    if pin and current_id:
        try:
            await context.bot.pin_chat_message(chat_id, current_id, disable_notification=True)
        except Exception as e:
            log.warning(f"group deal card pin failed for {deal.get('deal_id')}: {e}")
    return True


async def try_delete(msg):
    """Delete a message silently."""
    try:
        await msg.delete()
    except Exception:
        pass


async def _auto_delete(msg, seconds: int = 5):
    """Auto-delete a message after N seconds (fire-and-forget)."""
    await asyncio.sleep(seconds)
    await try_delete(msg)


async def send_temp_reply(msg, text: str, seconds: int = 5):
    """Send a reply that auto-deletes after N seconds."""
    try:
        sent = await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        asyncio.create_task(_auto_delete(sent, seconds))
    except Exception:
        pass


async def qans(q, text: str = None, alert: bool = False):
    """Safely answer a callback query — NEVER throws. Use everywhere."""
    try:
        if text:
            await q.answer(text, show_alert=alert)
        else:
            await q.answer()
    except Exception:
        pass


async def send_dm(user_id: int, text: str, markup=None,
                  parse_mode=ParseMode.MARKDOWN_V2):
    """Send a DM silently (handles blocked/not-started)."""
    if not _bot_app:
        return False
    try:
        await _bot_app.bot.send_message(
            user_id, text, parse_mode=parse_mode,
            reply_markup=markup, disable_web_page_preview=True,
        )
        return True
    except BadRequest as e:
        # Dynamic usernames/product names occasionally contain a MarkdownV2-reserved
        # character that slips through a new message template. Retry as plain text so
        # a critical notification (especially a middleman accept button) is not lost.
        if parse_mode and "can't parse entities" in str(e).lower():
            try:
                await _bot_app.bot.send_message(
                    user_id, text, parse_mode=None,
                    reply_markup=markup, disable_web_page_preview=True,
                )
                log.warning(f"DM {user_id} required plain-text fallback after MarkdownV2 parse failure")
                return True
            except Exception as fallback_error:
                log.warning(f"DM {user_id} plain-text fallback failed: {fallback_error}")
        log.warning(f"DM {user_id} failed: {e}")
        return False
    except Forbidden as e:
        log.warning(f"DM {user_id} failed: {e}")
        return False
    except Exception as e:
        log.warning(f"DM {user_id} failed: {e}")
        return False


async def set_middleman_room_permissions(context, deal: dict, locked: bool) -> bool:
    """Best-effort room moderation; owner/admin accounts remain Telegram-protected."""
    chat_id = deal.get("room_chat_id")
    if not chat_id:
        return False
    permissions = ChatPermissions(
        can_send_messages=not locked,
        can_send_audios=not locked,
        can_send_documents=not locked,
        can_send_photos=not locked,
        can_send_videos=not locked,
        can_send_video_notes=not locked,
        can_send_voice_notes=not locked,
        can_send_polls=not locked,
        can_send_other_messages=not locked,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_topics=False,
    )
    ok = True
    try:
        await context.bot.set_chat_permissions(chat_id, permissions)
    except Exception as e:
        ok = False
        log.warning(f"room default permissions update failed for {chat_id}: {e}")

    # Default permissions do not always update existing members, so apply the same
    # policy to the three expected participants. Telegram owners/admins are skipped.
    for uid in (deal.get("buyer_id"), deal.get("seller_id"), deal.get("middleman_id")):
        if not uid:
            continue
        try:
            member = await context.bot.get_chat_member(chat_id, uid)
            if member.status in ("creator", "administrator", "left", "kicked"):
                continue
            await context.bot.restrict_chat_member(
                chat_id, uid, permissions=permissions,
                use_independent_chat_permissions=True,
            )
        except Exception as e:
            ok = False
            log.warning(f"room participant permissions update failed for {chat_id}/{uid}: {e}")
    return ok


async def record_room_event(deal_id: str, event: str, **data):
    """Keep system transitions in the same append-only transcript admins review."""
    await db.append_room_transcript(deal_id, {
        "type": "system",
        "event": event,
        "text": data.pop("text", event.replace("_", " ").title()),
        "at": now_iso(),
        **data,
    })


async def begin_manual_dispute(context, deal: dict, opener, issue: str):
    """Create the human-review ticket only after the opener submits the issue."""
    deal_id = deal["deal_id"]
    await db.update_deal(
        deal_id,
        status="disputed",
        room_disputed_at=now_iso(),
        room_chat_locked=True,
        room_ready_to_proceed=False,
    )
    ticket_code = await db.create_dispute({
        "deal_id": deal_id,
        "opened_by": opener.id,
        "ticket_code": f"DP-{deal_id}",
    })
    await db.add_dispute_message(deal_id, {
        "from": opener.username or str(opener.id),
        "from_id": opener.id,
        "type": "issue",
        "text": issue[:4000],
        "at": now_iso(),
    })

    RELAY.pop(deal.get("buyer_id"), None)
    RELAY.pop(deal.get("seller_id"), None)
    other_id = deal["seller_id"] if opener.id == deal["buyer_id"] else deal["buyer_id"]
    deal["status"] = "disputed"
    deal["room_disputed_at"] = deal.get("room_disputed_at") or now_iso()
    deal["room_chat_locked"] = True
    deal["room_ready_to_proceed"] = False
    await record_room_event(
        deal_id, "dispute_opened",
        user_id=opener.id,
        username=opener.username or str(opener.id),
        text="Dispute opened. Deal actions are frozen pending admin review.",
    )
    await set_middleman_room_permissions(context, deal, locked=True)
    if deal.get("room_chat_id"):
        await db.set_middleman_room_status(
            deal["room_chat_id"], "quarantined", deal_id,
        )
    if deal.get("room_chat_id"):
        try:
            notice = await context.bot.send_message(
                deal["room_chat_id"],
                "🚨 DISPUTE LOCKED\n"
                "This deal is frozen for admin review. Do not delete messages or send payment outside the recorded process.\n"
                "The complete room transcript is preserved for the admin.",
            )
            await record_room_event(
                deal_id, "dispute_room_notice",
                message_id=notice.message_id,
                text="Dispute lock notice posted in the private room.",
            )
        except Exception as e:
            log.warning(f"dispute room notice failed for {deal_id}: {e}")
    EVIDENCE_AWAIT[opener.id] = {"deal_id": deal_id, "count": 0}
    if other_id:
        EVIDENCE_AWAIT[other_id] = {"deal_id": deal_id, "count": 0}

    await send_dm(
        opener.id,
        f"✅ Dispute ticket {ticket_code} is open.\n"
        f"Send screenshots or additional evidence here. Type 'done' when finished.\n"
        f"An admin will review this manually.",
        parse_mode=None,
    )
    if other_id:
        await send_dm(
            other_id,
            f"⚠️ Dispute ticket {ticket_code} has been opened for Deal #{deal_id}.\n"
            f"Please send your explanation and any evidence in this chat. Type 'done' when finished.\n"
            f"An admin will review the case manually.",
            parse_mode=None,
        )

    if _bot_app:
        from telegram import InlineKeyboardButton as IKB
        admin_markup = InlineKeyboardMarkup([[
            IKB("💬 DM Buyer", url=f"tg://user?id={deal['buyer_id']}"),
            IKB("💬 DM Seller", url=f"tg://user?id={deal['seller_id']}"),
        ]])
        try:
            await _bot_app.bot.send_message(
                TELEGRAM_ADMIN_ID,
                f"🚨 MANUAL DISPUTE OPENED\n"
                f"Ticket: {ticket_code}\n"
                f"Deal: #{deal_id}\n"
                f"Opened by: @{opener.username or opener.id}\n"
                f"Buyer: @{deal.get('buyer_un') or deal.get('buyer_id')}\n"
                f"Seller: @{deal.get('seller_un') or deal.get('seller_id')}\n\n"
                f"Issue:\n{issue[:1200]}\n\n"
                f"Open the Disputes tab in the admin panel to review evidence and resolve.",
                parse_mode=None,
                reply_markup=admin_markup,
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.warning(f"manual dispute admin notify failed: {e}")

    return ticket_code


def _room_role_for_user(deal: dict, user_id: int) -> str | None:
    roles = _room_roles_for_user(deal, user_id)
    return roles[0] if roles else None


def _room_roles_for_user(deal: dict, user_id: int) -> list[str]:
    roles = []
    if user_id == deal.get("buyer_id"):
        roles.append("buyer")
    if user_id == deal.get("seller_id"):
        roles.append("seller")
    if user_id == deal.get("middleman_id"):
        roles.append("middleman")
    return roles


def build_middleman_room_card(deal: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Private per-deal room card with all actionable workflow buttons."""
    def mark(key: str) -> str:
        return "✅" if deal.get(key) else "⬜"

    deal_id = str(deal.get("deal_id", ""))
    product_name = str(deal.get("product_name") or "Standard Escrow")[:20]
    tos = str(deal.get("toc") or "Standard Escrow Protection Terms")[:50]
    amount = deal.get("amount", 0)
    fee_pct = deal.get("fee_percent", 1.0)
    fee_payer = str(deal.get("fee_payer") or "buyer").lower()
    _, _, fee_amt = compute_fee(amount, fee_pct, fee_payer)

    buyer = esc(str(deal.get("buyer_un") or deal.get("buyer_id") or "Buyer"))
    seller = esc(str(deal.get("seller_un") or deal.get("seller_id") or "Seller"))
    middleman = esc(
        str(deal.get("middleman_un") or deal.get("middleman_id") or "assigned middleman")
    )
    status = deal.get("status", "awaiting_payment")
    status_label = DEAL_STATUS_LABELS.get(status, status.replace("_", " ").title())
    time_taken = format_time_taken(deal)

    all_confirmed = all(deal.get(key) for key in (
        "room_buyer_confirmed", "room_seller_confirmed", "room_middleman_confirmed",
    ))
    if status == "disputed":
        phase = (
            "🚨 *DISPUTE LOCKED*\n"
            "All deal actions are frozen\\. An admin is reviewing the preserved transcript\\."
        )
    elif all_confirmed:
        phase = (
            "✅ *ALL PARTICIPANTS CONFIRMED*\n"
            "🔐 Continue using the buttons below in this private room\\.\n"
            "Do not send payment or deliver outside this room's protected flow\\."
        )
    else:
        phase = (
            "⏳ *CONFIRMATION REQUIRED*\n"
            "Press only your assigned confirmation button below\\. The room stays read\\-only until all "
            "three confirmations are green\\."
        )
    text = (
        f"🛡️ *VAULT\\-PAY PRIVATE DEAL ROOM*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *Deal ID:* *\\#{esc(deal_id)}*\n"
        f"📦 *Product:* {esc(product_name)}\n"
        f"💰 *Amount:* ₹{amount:,}\n"
        f"📋 *ToS:* _{esc(tos)}_\n"
        f"🏦 *Fee \\(1%\\):* ₹{fee_amt:,} \\(Paid by: {esc(fee_payer.title())}\\)\n"
        f"👤 *Buyer:* @{buyer}\n"
        f"📦 *Seller:* @{seller}\n"
        f"🛡️ *Middleman:* @{middleman} \\(verified\\)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Status:* *{esc(status_label)}*\n"
        f"⏱ *Time Taken:* *{esc(time_taken)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{mark('room_buyer_confirmed')} Buyer confirmed terms\n"
        f"{mark('room_seller_confirmed')} Seller confirmed terms\n"
        f"{mark('room_middleman_confirmed')} Middleman acknowledged deal\n\n"
        f"{phase}\n\n"
        f"All room actions and messages are preserved for admin review\\."
    )
    rows = []
    if status not in ("disputed", "completed", "cancelled", "expired"):
        if not all_confirmed:
            role_buttons = [
                ("buyer", "✅ Buyer Confirm"),
                ("seller", "✅ Seller Confirm"),
                ("middleman", "🛡️ Middleman Acknowledge"),
            ]
            current = []
            for role, label in role_buttons:
                current.append((label, f"room:confirm:{deal_id}:{role}"))
                if len(current) == 2:
                    rows.append(current)
                    current = []
            if current:
                rows.append(current)
            rows.append([("❌ Cancel Deal", f"room:cancel:{deal_id}")])
        else:
            mm_st = MM_STATE.get(deal_id, {})
            if status in ("awaiting_payment", "mm_connecting"):
                if mm_st.get("mm_upi") and mm_st.get("status") == "buyer_paid_reported":
                    rows.append([(
                        "🛡️ Middleman: Confirm Payment Received",
                        f"mm:mm_recv:{deal_id}",
                    )])
                elif mm_st.get("mm_upi"):
                    rows.append([(
                        "💳 Buyer: I Paid Middleman",
                        f"mm:buyer_paid:{deal_id}",
                    )])
                rows.append([("❌ Cancel Deal", f"room:cancel:{deal_id}")])
            elif status == "mm_payment_received":
                rows.append([(
                    "🔓 Buyer: Release Asset",
                    f"mm:release:{deal_id}",
                )])
            elif status == "mm_asset_released":
                if mm_st.get("seller_upi"):
                    rows.append([(
                        "💸 Middleman: Mark Seller Paid",
                        f"mm:paid_seller:{deal_id}",
                    )])
                else:
                    rows.append([(
                        "📦 Seller: Provide Payment Details",
                        f"mm:seller_connect:{deal_id}",
                    )])
            elif status == "mm_seller_paid":
                rows.append([(
                    "✅ Seller: Confirm Receipt",
                    f"mm:seller_recv_ask:{deal_id}",
                )])
    if status not in ("completed", "cancelled", "expired") and status != "disputed":
        rows.append([("⚠️ Raise Dispute", f"room:dispute:{deal_id}")])
    markup = kb(rows)
    return text, markup


async def _send_room_invite(context, room_chat_id: int, deal_id: str, role: str) -> str:
    """Create a short-lived, one-person invite; membership is still checked on join."""
    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=room_chat_id,
            name=f"vp-{deal_id}-{role}",
            expire_date=datetime.now(timezone.utc) + timedelta(hours=2),
            member_limit=1,
        )
        return invite.invite_link
    except Exception as e:
        log.warning(f"room invite failed for {room_chat_id}/{deal_id}/{role}: {e}")
        return ""


async def assign_middleman_room(context, deal: dict, mm_id: int, mm_un: str) -> bool:
    """Reserve a room, persist the assignment, and DM all three participants their link."""
    room = await db.claim_middleman_room(deal["deal_id"])
    if not room:
        await send_dm(
            mm_id,
            f"⚠️ *Deal \\#{esc(deal['deal_id'])}*\n\n"
            f"All private deal rooms are currently occupied\\. You are still assigned as the "
            f"middleman, but room allocation is waiting for the next available slot\\.",
        )
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"⚠️ No private room available for middleman Deal \\#{esc(deal['deal_id'])}\\. "
            f"Please add/register more rooms or finish an existing deal\\.",
        )
        return False

    room_chat_id = room["chat_id"]
    await db.update_deal(
        deal["deal_id"],
        middleman_id=mm_id,
        middleman_un=mm_un,
        room_chat_id=room_chat_id,
        room_buyer_joined=False,
        room_seller_joined=False,
        room_middleman_joined=False,
        room_buyer_confirmed=False,
        room_seller_confirmed=False,
        room_middleman_confirmed=False,
        room_transcript_json="[]",
        room_ready_to_proceed=False,
        room_chat_locked=True,
        room_disputed_at="",
        room_confirmation_prompted=False,
    )
    deal.update({
        "middleman_id": mm_id,
        "middleman_un": mm_un,
        "room_chat_id": room_chat_id,
        "room_buyer_joined": False,
        "room_seller_joined": False,
        "room_middleman_joined": False,
        "room_buyer_confirmed": False,
        "room_seller_confirmed": False,
        "room_middleman_confirmed": False,
        "room_ready_to_proceed": False,
        "room_chat_locked": True,
        "room_disputed_at": "",
        "room_confirmation_prompted": False,
    })

    # A Telegram account may hold more than one role in a test/manual deal.
    # Create one single-use invite per unique account, otherwise the first link
    # consumes the member limit and the other role links can never be used.
    participant_roles: dict[int, list[str]] = {}
    for role, uid in (
        ("buyer", deal.get("buyer_id")),
        ("seller", deal.get("seller_id")),
        ("middleman", mm_id),
    ):
        if uid:
            participant_roles.setdefault(uid, []).append(role)
    links_by_user: dict[int, str] = {}
    for uid, roles in participant_roles.items():
        links_by_user[uid] = await _send_room_invite(
            context, room_chat_id, deal["deal_id"], "-".join(roles)
        )
    room_name = esc(room.get("title") or str(room_chat_id))
    common = (
        f"🛡️ *Private Deal Room Ready — Deal \\#{esc(deal['deal_id'])}*\n"
        f"Room: *{room_name}*\n"
        f"Buyer: @{esc(deal.get('buyer_un'))} \\| Seller: @{esc(deal.get('seller_un'))}\n"
        f"Middleman: @{esc(mm_un)}\n\n"
        f"Join only through your personal link\\. The bot will verify your Telegram account "
        f"before activating the room\\.\n"
    )
    for uid, roles in participant_roles.items():
        link = links_by_user.get(uid)
        if link:
            role_label = " + ".join(role.title() for role in roles)
            await send_dm(
                uid,
                common + f"\nYour role(s): {role_label}\n"
                f"Your invite link:\n{link}\n\n"
                f"After joining, wait for the other two participants and the pinned deal card\\.",
            )
    return True


async def notify_middlemen_for_deal(context, deal: dict):
    """Notify every active middleman as soon as a manual-MM deal is created.

    The admin is a fallback recipient so a test/manual operation still has an accept
    button even when the middlemen table has not been populated yet.
    """
    mms = await db.get_active_middlemen()
    recipients = []
    for mm in mms:
        if mm.get("tg_id") and mm["tg_id"] not in recipients:
            recipients.append(mm["tg_id"])
    if TELEGRAM_ADMIN_ID and TELEGRAM_ADMIN_ID not in recipients:
        recipients.append(TELEGRAM_ADMIN_ID)
    if not recipients:
        return 0

    markup = kb([[("✅ Accept & Enter This Deal", f"mm:enter:{deal['deal_id']}")]])
    sent = 0
    for mm_id in recipients:
        ok = await send_dm(
            mm_id,
            f"NEW MANUAL MIDDLEMAN DEAL — Deal #{deal['deal_id']}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Product: {deal.get('product_name') or 'N/A'}\n"
            f"Amount: ₹{deal.get('amount', 0):,}\n"
            f"Buyer: @{deal.get('buyer_un') or deal.get('buyer_id')}\n"
            f"Seller: @{deal.get('seller_un') or deal.get('seller_id')}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Accepting assigns you as the verified middleman and sends the private-room "
            f"invite links to all three participants.",
            markup=markup,
            parse_mode=None,
        )
        sent += int(bool(ok))
    return sent


async def _remove_room_member_if_kickable(context, chat_id: int, user_id: int) -> bool:
    """Remove a deal participant unless Telegram marks them as creator/admin.

    Protected owners/admins are intentionally left in place and are not treated as
    cleanup failures; they are permanent support members of the room.
    """
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ("creator", "administrator", "left", "kicked"):
            return True
        await context.bot.ban_chat_member(chat_id, user_id)
        await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        return True
    except Exception as e:
        log.warning(f"room member cleanup failed for {chat_id}/{user_id}: {e}")
        return False


async def maybe_publish_middleman_room(context, chat_id: int):
    """When buyer, seller and middleman have joined, publish and pin the room card."""
    deal = await db.get_deal_by_room_chat(chat_id)
    if not deal:
        return
    try:
        member_ids = set()
        for uid in (deal.get("buyer_id"), deal.get("seller_id"), deal.get("middleman_id")):
            if not uid:
                continue
            member = await context.bot.get_chat_member(chat_id, uid)
            if member.status not in ("left", "kicked"):
                member_ids.add(member.user.id)
        joined = {
            "room_buyer_joined": deal.get("buyer_id") in member_ids,
            "room_seller_joined": deal.get("seller_id") in member_ids,
            "room_middleman_joined": deal.get("middleman_id") in member_ids,
        }
        await db.update_deal(deal["deal_id"], **joined)
        deal.update(joined)
    except Exception as e:
        log.warning(f"room member check failed for {chat_id}: {e}")
        return

    if not all(deal.get(k) for k in (
        "room_buyer_joined", "room_seller_joined", "room_middleman_joined"
    )):
        return

    await set_middleman_room_permissions(context, deal, locked=not deal.get("room_ready_to_proceed"))
    card_text, card_markup = build_middleman_room_card(deal)
    try:
        if deal.get("room_message_id"):
            await context.bot.edit_message_text(
                card_text, chat_id=chat_id, message_id=deal["room_message_id"],
                parse_mode=ParseMode.MARKDOWN_V2, reply_markup=card_markup,
            )
        else:
            message = await context.bot.send_message(
                chat_id, card_text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=card_markup,
            )
            await context.bot.pin_chat_message(chat_id, message.message_id, disable_notification=True)
            await db.update_deal(deal["deal_id"], room_message_id=message.message_id)
    except Exception as e:
        log.warning(f"room card publish failed for {chat_id}: {e}")

    # Existing rooms used to return before sending this prompt. Keep this
    # independent from card editing so a stale/invalid old card cannot suppress
    # the confirmation guidance.
    if not deal.get("room_confirmation_prompted") and not deal.get("room_ready_to_proceed"):
        try:
            prompt = await context.bot.send_message(
                chat_id,
                "✅ ALL PARTICIPANTS HAVE JOINED\n\n"
                "Step 1/3 — Buyer: confirm the terms.\n"
                "Step 2/3 — Seller: confirm the terms.\n"
                "Step 3/3 — Middleman: acknowledge the deal.\n\n"
                "The room is read-only until all three confirmations are green. "
                "Use only the assigned button on the pinned card.",
            )
            await db.update_deal(
                deal["deal_id"],
                room_confirmation_prompted=True,
                room_chat_locked=True,
            )
            await record_room_event(
                deal["deal_id"], "confirmation_prompt_posted",
                message_id=prompt.message_id,
                text="All participants joined. Confirmation steps posted in the room.",
            )
            await send_dm(
                deal["buyer_id"],
                f"✅ All participants joined private Deal Room #{esc(deal['deal_id'])}.\n"
                "Step 1/3: press Buyer Confirm on the pinned room card.",
                parse_mode=None,
            )
            await send_dm(
                deal["seller_id"],
                f"✅ All participants joined private Deal Room #{esc(deal['deal_id'])}.\n"
                "Step 2/3: press Seller Confirm on the pinned room card.",
                parse_mode=None,
            )
            await send_dm(
                deal["middleman_id"],
                f"🛡️ All participants joined private Deal Room #{esc(deal['deal_id'])}.\n"
                "Step 3/3: press Middleman Acknowledge on the pinned room card.",
                parse_mode=None,
            )
        except Exception as e:
            log.warning(f"room confirmation prompt failed for {chat_id}: {e}")


async def send_or_update_room_flow(context, deal_id: str, stage: str, q=None):
    """Deliver or update the dedicated action-buttons message inside the private deal room."""
    deal = await db.get_deal(deal_id)
    if not deal:
        return
    mm_st = MM_STATE.get(deal_id, {})
    target_chat_id = deal.get("room_chat_id") or deal.get("chat_id")
    if not target_chat_id:
        return

    amount = deal.get("amount", 0)
    fee_pct = deal.get("fee_percent", 1.0)
    fee_payer = str(deal.get("fee_payer") or "buyer").lower()
    buyer_debit, seller_credit, fee_amt = compute_fee(amount, fee_pct, fee_payer)
    buyer_un = esc(str(deal.get("buyer_un") or deal.get("buyer_id") or "Buyer"))
    seller_un = esc(str(deal.get("seller_un") or deal.get("seller_id") or "Seller"))
    mm_un = esc(str(deal.get("middleman_un") or mm_st.get("mm_un") or "Middleman"))
    prod_name = esc(str(deal.get("product_name") or "Standard Escrow"))
    mm_upi = str(mm_st.get("mm_upi") or deal.get("mm_upi") or "")
    seller_upi = str(mm_st.get("seller_upi") or deal.get("seller_upi") or "")

    text = ""
    markup = None

    if stage in ("waiting_buyer_pay", "awaiting_payment"):
        text = (
            f"💳 *PAYMENT TO MIDDLEMAN — DEAL \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 *Product:* {prod_name}\n"
            f"💰 *Deal Amount:* ₹{amount:,}\n"
            f"🏦 *Escrow Fee \\(1%\\):* ₹{fee_amt:,} \\({esc(fee_payer)} pays\\)\n"
            f"💸 *Total to Pay:* *₹{buyer_debit:,}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 *Middleman UPI ID:*\n"
            f"`{esc(mm_upi)}`\n"
            f"_\\(tap on UPI ID to copy\\)_\n\n"
            f"👇 *Buyer @{buyer_un}*:\n"
            f"1\\. Pay *₹{buyer_debit:,}* to the UPI ID above\\.\n"
            f"2\\. Once transferred, click *✅ I Have Paid* below\\."
        )
        markup = kb([
            [("✅ I Have Paid", f"mm:buyer_paid:{deal_id}")],
            [("⚠️ Raise Dispute", f"room:dispute:{deal_id}"),
             ("❌ Cancel Deal", f"room:cancel:{deal_id}")],
        ])

    elif stage == "buyer_paid_reported":
        text = (
            f"⚡ *BUYER REPORTED PAYMENT — DEAL \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *Amount:* ₹{buyer_debit:,}\n"
            f"👤 *Buyer:* @{buyer_un} has marked payment sent\\!\n"
            f"🏦 *Paid to Middleman UPI:* `{esc(mm_upi)}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ *Middleman @{mm_un}*:\n"
            f"Please check your bank/UPI app for *₹{buyer_debit:,}*\\.\n"
            f"Once received in your account, click *✅ Payment Received* below:"
        )
        markup = kb([
            [("🛡️ Payment Received (Middleman)", f"mm:mm_recv:{deal_id}")],
            [("⚠️ Raise Dispute", f"room:dispute:{deal_id}")],
        ])

    elif stage == "payment_received":
        text = (
            f"✅ *PAYMENT SECURED IN ESCROW — DEAL \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *₹{amount:,}* is safely locked with Middleman @{mm_un}\\.\n\n"
            f"📦 *Seller @{seller_un}*:\n"
            f"Payment is safely locked in escrow\\! Deliver your product/service to the buyer in this room now\\.\n\n"
            f"👤 *Buyer @{buyer_un}*:\n"
            f"After verifying your product/service, click *🔓 Release Asset* below:"
        )
        markup = kb([
            [("🔓 Release Asset", f"mm:release_ask:{deal_id}")],
            [("⚠️ Raise Dispute", f"room:dispute:{deal_id}")],
        ])

    elif stage == "release_ask":
        text = (
            f"⚠️ *CONFIRM ASSET RELEASE — DEAL \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Are you sure you want to release *₹{seller_credit:,}* to Seller @{seller_un}?\n\n"
            f"⚠️ *Warning:* Only confirm if you have completely received and verified your product/service\\. Once released, funds cannot be reversed\\!"
        )
        markup = kb([
            [("✅ Yes, Release Funds to Seller", f"mm:release_confirm:{deal_id}")],
            [("❌ No, Go Back", f"mm:release_cancel:{deal_id}")],
        ])

    elif stage in ("asset_released", "waiting_seller_upi"):
        text = (
            f"🔓 *ASSET RELEASED BY BUYER — DEAL \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Buyer @{buyer_un} verified delivery and released the funds\\!\n\n"
            f"📦 *Seller @{seller_un}*:\n"
            f"You will receive *₹{seller_credit:,}*\\.\n"
            f"Type your UPI ID directly in this room \\(e\\.g\\. `name@bank`\\) or send in bot DM:"
        )
        bun = _bot_username
        btn_rows = []
        if bun:
            btn_rows.append([InlineKeyboardButton("💬 Provide UPI via Bot DM", url=f"https://t.me/{bun}?start=mm_S_{deal_id}")])
        btn_rows.append([InlineKeyboardButton("💳 Seller: Connect in DM", callback_data=f"mm:seller_connect:{deal_id}")])
        btn_rows.append([InlineKeyboardButton("⚠️ Raise Dispute", callback_data=f"room:dispute:{deal_id}")])
        markup = InlineKeyboardMarkup(btn_rows)

    elif stage == "waiting_seller_pay":
        text = (
            f"💸 *SELLER PAYOUT DETAILS — DEAL \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 *Seller:* @{seller_un}\n"
            f"🏦 *Seller UPI ID:*\n"
            f"`{esc(seller_upi)}`\n"
            f"_\\(tap to copy\\)_\n"
            f"💰 *Amount to Send:* *₹{seller_credit:,}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ *Middleman @{mm_un}*:\n"
            f"Transfer *₹{seller_credit:,}* to the seller's UPI above\\.\n"
            f"After sending, click *💸 Payment Sent to Seller* below:"
        )
        markup = kb([
            [("💸 Payment Sent to Seller", f"mm:paid_seller:{deal_id}")],
            [("⚠️ Raise Dispute", f"room:dispute:{deal_id}")],
        ])

    elif stage == "seller_paid":
        text = (
            f"💸 *PAYOUT SENT BY MIDDLEMAN — DEAL \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Middleman @{mm_un} has sent *₹{seller_credit:,}* to seller UPI `{esc(seller_upi)}`\\.\n\n"
            f"📦 *Seller @{seller_un}*:\n"
            f"Please check your UPI/bank app for *₹{seller_credit:,}*\\.\n"
            f"Once received, click *✅ I Have Received Payment* below:"
        )
        markup = kb([
            [("✅ I Have Received Payment", f"mm:seller_recv_ask:{deal_id}")],
            [("⚠️ Raise Dispute", f"room:dispute:{deal_id}")],
        ])

    elif stage == "seller_recv_ask":
        text = (
            f"🔐 *FINAL CONFIRMATION — DEAL \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Seller @{seller_un}, confirm that you have received *₹{seller_credit:,}* in your bank account?\n\n"
            f"⚠️ *Notice:* This will finalize and close Deal \\#{esc(deal_id)}\\. This action is permanent\\!"
        )
        markup = kb([
            [("🎉 Yes, Confirm & Complete Deal", f"mm:seller_recv:{deal_id}")],
            [("❌ No, Not Yet Received", f"mm:seller_recv_no:{deal_id}")],
        ])

    elif stage == "completed":
        text = (
            f"🎉 *DEAL \\#{esc(deal_id)} COMPLETED SUCCESSFULLY\\!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ *Buyer:* @{buyer_un} received product\n"
            f"✅ *Seller:* @{seller_un} received ₹{seller_credit:,}\n"
            f"🛡️ *Middleman:* @{mm_un} verified escrow\n"
            f"💰 *Total Amount:* ₹{amount:,}\n\n"
            f"Thank you for using Vault\\-Pay Escrow\\! Room will be closed\\."
        )
        markup = None

    elif stage == "cancelled":
        text = (
            f"❌ *DEAL \\#{esc(deal_id)} CANCELLED*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"This escrow deal has been terminated\\.\n"
            f"No funds were moved\\. The room is now closed\\."
        )
        markup = None

    elif stage == "disputed":
        text = (
            f"🚨 *DEAL \\#{esc(deal_id)} UNDER ACTIVE DISPUTE*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Deal actions are frozen\\. An administrator has been notified and is reviewing the preserved room transcript\\."
        )
        markup = None

    if not text:
        return

    # Try in-place edit if callback query message matches
    msg_id = deal.get("room_action_msg_id") or mm_st.get("flow_msg_id")
    edited = False
    if q and q.message and q.message.chat_id == target_chat_id:
        try:
            await safe_edit(q.message, text, markup)
            msg_id = q.message.message_id
            mm_st["flow_msg_id"] = msg_id
            await db.update_deal(deal_id, room_action_msg_id=msg_id)
            edited = True
        except Exception as e:
            log.warning(f"safe_edit failed in send_or_update_room_flow: {e}")

    if not edited and msg_id:
        try:
            await context.bot.edit_message_text(
                text,
                chat_id=target_chat_id,
                message_id=msg_id,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=markup,
            )
            edited = True
        except Exception as e:
            log.warning(f"edit_message_text failed in send_or_update_room_flow: {e}")

    if not edited:
        try:
            sent = await context.bot.send_message(
                target_chat_id,
                text,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=markup,
            )
            msg_id = sent.message_id
            mm_st["flow_msg_id"] = msg_id
            await db.update_deal(deal_id, room_action_msg_id=msg_id)
            try:
                await context.bot.pin_chat_message(target_chat_id, msg_id, disable_notification=True)
            except Exception:
                pass
        except Exception as e:
            log.warning(f"send_message failed in send_or_update_room_flow: {e}")


async def refresh_middleman_room_card(context, deal_id: str):
    """Keep the private-room card in sync after each recorded workflow step."""
    deal = await db.get_deal(deal_id)
    if not deal or not deal.get("room_chat_id") or not deal.get("room_message_id"):
        return
    card_text, card_markup = build_middleman_room_card(deal)
    try:
        await context.bot.edit_message_text(
            card_text,
            chat_id=deal["room_chat_id"],
            message_id=deal["room_message_id"],
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=card_markup,
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning(f"room card refresh failed for {deal_id}: {e}")
    except Exception as e:
        log.warning(f"room card refresh failed for {deal_id}: {e}")
async def send_room_transcript(deal: dict, recipients=None) -> int:
    """Send the preserved room transcript as a PDF before Telegram cleanup."""
    if not _bot_app or not deal:
        return 0
    try:
        pdf_bytes = build_transcript_pdf(deal)
    except Exception as e:
        log.warning(f"transcript PDF generation failed for {deal.get('deal_id')}: {e}")
        return 0

    target_ids = recipients or (
        deal.get("buyer_id"),
        deal.get("seller_id"),
        deal.get("middleman_id"),
        TELEGRAM_ADMIN_ID,
    )
    filename = f"vault-pay-deal-{deal.get('deal_id', 'unknown')}-transcript.pdf"
    caption = (
        f"Vault-Pay Deal #{deal.get('deal_id', 'unknown')} transcript\n"
        "Times are shown in IST (Asia/Kolkata)."
    )
    delivered = 0
    for uid in dict.fromkeys(filter(None, target_ids)):
        try:
            document = io.BytesIO(pdf_bytes)
            document.name = filename
            await _bot_app.bot.send_document(
                chat_id=uid,
                document=document,
                caption=caption,
            )
            delivered += 1
        except Exception as e:
            log.warning(f"transcript PDF delivery failed for {deal.get('deal_id')} to {uid}: {e}")
    return delivered


async def cleanup_middleman_room(context, deal: dict):
    """Remove participants and clear recorded messages before reusing a room."""
    room_chat_id = deal.get("room_chat_id")
    if not room_chat_id:
        return
    latest = await db.get_deal(deal["deal_id"]) or deal
    # Deliver the preserved transcript before deleting room messages. This also
    # covers admin-triggered cleanup and disputed-room cleanup.
    try:
        await send_room_transcript(latest or deal)
    except Exception as e:
        log.warning(f"room transcript delivery failed for {deal['deal_id']}: {e}")
    try:
        entries = json.loads(latest.get("room_transcript_json") or "[]")
    except Exception:
        entries = []
    message_ids = {e.get("message_id") for e in entries if e.get("message_id")}
    if latest.get("room_message_id"):
        message_ids.add(latest["room_message_id"])
    cleanup_ok = True
    for message_id in message_ids:
        try:
            await context.bot.delete_message(room_chat_id, int(message_id))
        except BadRequest as e:
            # Re-running an explicit admin cleanup is safe when a message was
            # already removed during an earlier partial cleanup attempt.
            if "message to delete not found" not in str(e).lower():
                cleanup_ok = False
        except Exception:
            cleanup_ok = False
    for uid in (deal.get("buyer_id"), deal.get("seller_id"), deal.get("middleman_id")):
        if not uid:
            continue
        try:
            if not await _remove_room_member_if_kickable(context, room_chat_id, uid):
                cleanup_ok = False
        except Exception:
            cleanup_ok = False
    # Always return room to available so it remains permanently active for next deals
    await db.set_middleman_room_status(room_chat_id, "available", "")
    log.info(f"Room {room_chat_id} sanitization completed and returned to 'available' pool (cleanup_ok={cleanup_ok})")
    return True


async def handle_room_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Persist a complete, admin-reviewable record of messages from a private room."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user or chat.type == ChatType.PRIVATE:
        return
    deal = await db.get_deal_by_room_chat(chat.id)
    if not deal:
        return
    if user.id not in (deal.get("buyer_id"), deal.get("seller_id"), deal.get("middleman_id"), TELEGRAM_ADMIN_ID):
        try:
            await context.bot.delete_message(chat.id, msg.message_id)
        except Exception:
            pass
        return
    text = msg.text or msg.caption or ""

    # Check if seller is providing their UPI ID inside the private deal room
    if deal.get("status") == "mm_asset_released" and user.id == deal.get("seller_id") and text and "@" in text and len(text.strip()) >= 5:
        upi = text.strip()
        deal_id = deal["deal_id"]
        mm_st = MM_STATE.get(deal_id, {})
        mm_st["seller_upi"] = upi
        mm_st["status"] = "waiting_seller_pay"
        MM_STATE[deal_id] = mm_st
        await db.update_deal(deal_id, seller_upi=upi)
        deal["seller_upi"] = upi
        MM_DM_AWAIT.pop(user.id, None)
        await send_or_update_room_flow(context, deal_id, stage="waiting_seller_pay")
        await publish_group_deal_card(context, deal, pin=True)
        mm_id = mm_st.get("mm_id") or deal.get("middleman_id")
        if mm_id:
            await send_dm(
                mm_id,
                f"📦 *Seller UPI for Deal \\#{esc(deal_id)}*\n"
                f"UPI: `{esc(upi)}`\n"
                f"Amount to send: *₹{deal['amount']:,}*\n\n"
                f"Send payment, then confirm using the button in the private deal room\\.",
            )
    file_id = ""
    content_type = "text" if text else "message"
    file_name = ""
    mime_type = ""
    if msg.photo:
        file_id = msg.photo[-1].file_id
        content_type = "photo"
    elif msg.document:
        file_id = msg.document.file_id
        content_type = "document"
        file_name = msg.document.file_name or ""
        mime_type = msg.document.mime_type or ""
    elif msg.video:
        file_id = msg.video.file_id
        content_type = "video"
        mime_type = msg.video.mime_type or ""
    elif msg.audio:
        file_id = msg.audio.file_id
        content_type = "audio"
        file_name = msg.audio.file_name or ""
        mime_type = msg.audio.mime_type or ""
    elif msg.voice:
        file_id = msg.voice.file_id
        content_type = "voice"
    elif msg.animation:
        file_id = msg.animation.file_id
        content_type = "animation"
    elif msg.sticker:
        file_id = msg.sticker.file_id
        content_type = "sticker"
    if not text and not file_id:
        return

    file_ref_id = ""
    storage_msg_id = None
    if file_id:
        # Generate unique file reference ID for transcript (e.g. MEDIA-4095-7B3F9A1C)
        seed = f"{deal['deal_id']}-{msg.message_id}-{file_id[:16]}-{int(datetime.now(timezone.utc).timestamp())}"
        short_hash = hashlib.sha256(seed.encode()).hexdigest()[:8].upper()
        file_ref_id = f"MEDIA-{deal['deal_id']}-{short_hash}"

        # Mirror media silently to the private storage channel if configured
        storage_channel = (await db.get_media_storage_channel()) or -1004495812759
        if storage_channel:
            role_label = (_room_roles_for_user(deal, user.id) or ["admin" if user.id == TELEGRAM_ADMIN_ID else "participant"])[0].upper()
            caption_lines = [
                "🔒 *Vault-Pay Encrypted Media Archive*",
                f"• *Deal ID:* `#{deal['deal_id']}`",
                f"• *Ref ID:* `{file_ref_id}`",
                f"• *Sender:* @{user.username or user.id} \\(`{user.id}`\\) \\| *{role_label}*",
                f"• *Type:* *{content_type.upper()}*",
            ]
            if file_name:
                caption_lines.append(f"• *File:* `{esc(file_name)}`")
            if text:
                caption_lines.append(f"• *Caption:* {esc(text[:400])}")
            caption_lines.append("\n⚠️ *Auto-Purge Notice:* This media file will self\\-destruct automatically in 24 hours\\.")
            storage_caption = "\n".join(caption_lines)

            # Stage 1: Try copy_message with MarkdownV2
            try:
                copied = await context.bot.copy_message(
                    chat_id=storage_channel,
                    from_chat_id=chat.id,
                    message_id=msg.message_id,
                    caption=storage_caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_notification=True,
                )
                storage_msg_id = copied.message_id
            except Exception as e_copy1:
                log.warning(f"copy_message MarkdownV2 failed: {e_copy1}, trying plain text caption")
                # Stage 2: Try copy_message with plain text caption
                plain_caption = (
                    f"🔒 Vault-Pay Encrypted Media Archive\n"
                    f"• Deal ID: #{deal['deal_id']}\n"
                    f"• Ref ID: {file_ref_id}\n"
                    f"• Sender: @{user.username or user.id} ({user.id}) | {role_label}\n"
                    f"• Type: {content_type.upper()}\n"
                )
                if file_name:
                    plain_caption += f"• File: {file_name}\n"
                if text:
                    plain_caption += f"• Caption: {text[:400]}\n"
                plain_caption += "\n⚠️ Auto-Purge Notice: This media file will self-destruct automatically in 24 hours."

                try:
                    copied = await context.bot.copy_message(
                        chat_id=storage_channel,
                        from_chat_id=chat.id,
                        message_id=msg.message_id,
                        caption=plain_caption,
                        disable_notification=True,
                    )
                    storage_msg_id = copied.message_id
                except Exception as e_copy2:
                    log.warning(f"copy_message plain text failed: {e_copy2}, trying direct forward")
                    # Stage 3: Direct forward_message
                    try:
                        fwd = await context.bot.forward_message(
                            chat_id=storage_channel,
                            from_chat_id=chat.id,
                            message_id=msg.message_id,
                            disable_notification=True,
                        )
                        storage_msg_id = fwd.message_id
                    except Exception as e_fwd:
                        log.error(f"All media forwarding methods failed for {file_ref_id} to {storage_channel}: {e_fwd}")

            if storage_msg_id:
                try:
                    # Schedule 24h auto-delete in DB (persists across bot restarts)
                    purge_dt = datetime.now(timezone.utc) + timedelta(hours=24)
                    await db.schedule_media_purge(
                        chat_id=storage_channel,
                        message_id=storage_msg_id,
                        purge_at=purge_dt.isoformat(),
                        ref_id=file_ref_id,
                        deal_id=deal["deal_id"],
                    )
                    log.info(f"Mirrored media {file_ref_id} to storage channel {storage_channel} (msg {storage_msg_id})")
                except Exception as e_purge:
                    log.warning(f"Failed to schedule purge for msg {storage_msg_id}: {e_purge}")

    await db.append_room_transcript(deal["deal_id"], {
        "type": "message",
        "message_id": msg.message_id,
        "user_id": user.id,
        "username": user.username or str(user.id),
        "text": text[:2000],
        "content_type": content_type,
        "file_id": file_id,
        "file_ref_id": file_ref_id,
        "file_name": file_name,
        "mime_type": mime_type,
        "storage_msg_id": storage_msg_id,
        "role": (_room_roles_for_user(deal, user.id) or ["admin" if user.id == TELEGRAM_ADMIN_ID else "participant"])[0],
        "at": now_iso(),
    })

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _check_abusive(user, text: str, deal_id: str = "", context_label: str = "chat") -> bool:
    """AI-judge upgrade: catch abusive/gaali language wherever a user types (dispute evidence,
    relay chat, initial statement). Warns the user, tells the admin, and after ABUSE_WARNING_LIMIT
    warnings auto-flags the account for admin review. Returns True if the message was abusive
    (callers still save/relay the message — this only adds a side-effect warning)."""
    import ai_judge
    if not ai_judge.detect_abusive_language(text):
        return False
    try:
        count = await db.increment_abuse_warning(user.id)
    except Exception:
        count = 1
    await send_dm(
        user.id,
        "⚠️ *Please keep it civil\\.* Abusive language was detected in your last message\\.\n"
        f"Warning {esc(str(count))}/{esc(str(ABUSE_WARNING_LIMIT))} \\— repeated abuse can affect how your dispute "
        "is judged and may lead to account restrictions\\.",
    )
    deal_ref = f" \\(Deal \\#{esc(deal_id)}\\)" if deal_id else ""
    await send_dm(
        TELEGRAM_ADMIN_ID,
        f"🤬 *Abusive language flagged*{deal_ref}\n"
        f"From: @{esc(user.username or str(user.id))} \\| Context: {esc(context_label)} \\| Warning {esc(str(count))}\n"
        f"\"{esc(text[:300])}\"",
    )
    if count >= ABUSE_WARNING_LIMIT:
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"🚨 *Repeat offender* \\— @{esc(user.username or str(user.id))} has hit "
            f"{esc(str(count))} abuse warnings\\. Consider reviewing their account\\.",
        )
    return True


def compute_fee(amount: int, fee_pct: float, fee_payer: str):
    fee_amt = round(amount * fee_pct / 100)
    if fee_payer == "buyer":
        return amount + fee_amt, amount, fee_amt
    else:
        return amount, amount - fee_amt, fee_amt


async def get_fee_pct() -> float:
    v = await db.get_setting("fee_percent", DEFAULT_FEE_PERCENT)
    try:
        return float(v)
    except Exception:
        return float(DEFAULT_FEE_PERCENT)


async def get_default_toc() -> str:
    v = await db.get_setting("default_toc", "")
    return v or "Standard escrow: Funds held by middleman and released only on buyer confirmation. No chargebacks."


async def is_test_mode_admin_only() -> bool:
    """Read the live test-mode switch, falling back to the deployment config."""
    value = await db.get_setting("test_mode_admin_only", TEST_MODE_ADMIN_ONLY)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


# ─── Deal card builders ────────────────────────────────────────────────────────

STEPS = ["role", "product", "amount", "fee", "toc", "payment", "mention", "waiting"]
STEP_PROGRESS = {
    "role":    "🟢⚪⚪⚪⚪⚪⚪  Step 1/7",
    "product": "🟢🟢⚪⚪⚪⚪⚪  Step 2/7",
    "amount":  "🟢🟢🟢⚪⚪⚪⚪  Step 3/7",
    "fee":     "🟢🟢🟢🟢⚪⚪⚪  Step 4/7",
    "toc":     "🟢🟢🟢🟢🟢⚪⚪  Step 5/7",
    "payment": "🟢🟢🟢🟢🟢🟢⚪  Step 6/7",
    "mention": "🟢🟢🟢🟢🟢🟢🟢  Step 7/7",
    "waiting": "✅✅✅✅✅✅✅  Awaiting counterparty",
}


def build_draft_card(d: dict) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build the group message for each deal creation step."""
    step = d.get("step", "role")
    role = d.get("role", "")
    role_emoji = "💰" if role == "buyer" else "📦" if role == "seller" else ""
    role_label = "Buyer" if role == "buyer" else "Seller" if role == "seller" else ""
    progress = esc(STEP_PROGRESS.get(step, ""))

    header = f"🏦 *VAULT\\-PAY ESCROW*\n{progress}\n"

    # ── Step 1: Role ──────────────────────────────────────────────────────────
    if step == "role":
        return (
            header + "\nSelect your role in this deal:\n\n"
            "💰 *Buyer* — you will be paying\n"
            "📦 *Seller* — you will be receiving",
            kb([
                [("💰 I'm the Buyer", "esc:role:buyer"),
                 ("📦 I'm the Seller", "esc:role:seller")],
                [("❌ Cancel", "esc:cancel")],
            ])
        )

    info = f"\n{role_emoji} *Role:* {esc(role_label)}"

    # ── Step 2: Product Name ───────────────────────────────────────────────────
    if step == "product":
        return (
            header + info + "\n\n"
            "📦 *What is the product/service being traded?*\n"
            "_Type the name \\(max 20 characters\\)_",
            kb([[("❌ Cancel", "esc:cancel")]])
        )

    if d.get("product_name"):
        info += f"  \\|  📦 {esc(d['product_name'][:20])}"
    if d.get("amount"):
        info += f"  \\|  *₹{d['amount']:,}*"

    # ── Step 3: Amount ────────────────────────────────────────────────────────
    if step == "amount":
        return (
            header + info + "\n\n"
            "💬 *Type the deal amount in INR* \\(numbers only\\):\n"
            "_e\\.g\\. 5000_",
            kb([[("❌ Cancel", "esc:cancel")]])
        )

    # ── Step 4: Fee ───────────────────────────────────────────────────────────
    if step == "fee":
        _fee_pct = 1.0
        fee_amt = round(d["amount"] * _fee_pct / 100)
        return (
            header + info + "\n\n"
            f"🏦 *Who pays the 1% platform fee?*\n"
            f"Fee: ₹{fee_amt:,}\n\n"
            f"• *Buyer pays:* sends ₹{d['amount']:,} \\+ ₹{fee_amt:,} \\= ₹{d['amount'] + fee_amt:,}\n"
            f"• *Seller pays:* receives ₹{d['amount']:,} \\- ₹{fee_amt:,} \\= ₹{d['amount'] - fee_amt:,}",
            kb([
                [("Buyer Pays Fee (1%)", "esc:fee:buyer"),
                 ("Seller Pays Fee (1%)", "esc:fee:seller")],
                [("❌ Cancel", "esc:cancel")],
            ])
        )

    fp = d.get("fee_payer", "buyer")
    fee_amt = round(d["amount"] * d.get("fee_pct", 1.0) / 100)
    info += f"  \\|  fee→{esc(fp)}"

    # ── Step 5: ToS ───────────────────────────────────────────────────────────
    if step == "toc":
        toc_text = str(d.get("default_toc") or "Standard escrow protection terms apply.")[:50]
        return (
            header + info + "\n\n"
            "📋 *Terms of Service* \\(max 50 chars\\):\n"
            f"_{esc(toc_text)}_\n\n"
            "Choose a ToS option:",
            kb([
                [("✅ Accept Default ToS", "esc:toc:default")],
                [("✏️ Write Custom ToS", "esc:toc:custom"),
                 ("⏭️ Skip ToS", "esc:toc:skip")],
                [("❌ Cancel", "esc:cancel")],
            ])
        )

    if step == "custom_toc":
        return (
            header + info + "\n\n"
            "✏️ *Type your custom Terms of Service below:*\n"
            "_Max 50 characters — send as a message in this group_",
            kb([[("❌ Cancel", "esc:cancel")]])
        )

    # ── Step 6: Choose Middleman Option ──────────────────────────────────────
    if step == "mm_mode":
        options = [("🛡️ Manual Middleman", "esc:mm_mode:manual")]
        description = "A verified human moderator handles the private deal room."
        if auto_ltc.enabled():
            options.append(("🧪 Auto MM · LTC regtest", "esc:mm_mode:auto"))
            description += "\nAuto MM uses test coins on a private Litecoin chain."
        return (
            header + info + "\n\n🛡️ *Step 6: Choose Middleman Option*\n" + esc(description),
            kb([options, [("❌ Cancel", "esc:cancel")]]),
        )

    cp_role = "Seller" if role == "buyer" else "Buyer"
    mm_type_val = d.get("mm_type", "manual")
    if mm_type_val == "auto":
        info += "  \\|  🤖 Auto MM"
    else:
        info += "  \\|  🛡️ Manual MM"

    # ── Step 7: Mention counterparty ─────────────────────────────────────────
    if step == "mention":
        return (
            header + info + "\n\n"
            f"👥 *@mention the {esc(cp_role)}* in this group:\n"
            f"_They will see an Accept button to confirm the deal_",
            kb([[("❌ Cancel", "esc:cancel")]])
        )

    # ── Step 8: Waiting for counterparty ─────────────────────────────────────
    if step == "waiting":
        cp_un = d.get("counterparty_un", "?")
        toc_line = ""
        if d.get("toc"):
            toc_line = f"\n📋 ToS: _{esc(d['toc'][:60])}_"
        return (
            header + info + toc_line + "\n\n"
            f"⏳ Waiting for @{esc(cp_un)} to accept\\.\\.\\.\n\n"
            f"👇 *@{esc(cp_un)}* — click below to join this deal:",
            kb([
                [("✅ I Accept This Deal", f"esc:accept:{d['initiator_id']}")],
                [("❌ Reject", f"esc:reject:{d['initiator_id']}")],
                [("🗑️ Cancel", "esc:cancel")],
            ])
        )

    return header + "\n_Processing\\.\\.\\._", None


def build_deal_card(deal: dict, bot_username: str = "") -> tuple[str, InlineKeyboardMarkup | None]:
    """Build the canonical group deal status card with all deal details."""
    status = deal.get("status", "awaiting_payment")
    amount = deal.get("amount", 0)
    fee_pct = deal.get("fee_percent", 1.0)
    fee_payer = str(deal.get("fee_payer") or "buyer").lower()
    buyer_debit, seller_credit, fee_amt = compute_fee(amount, fee_pct, fee_payer)

    deal_id = str(deal.get("deal_id", ""))
    raw_prod = deal.get("product_name") or "Standard Escrow"
    product_name = str(raw_prod)[:20]
    raw_toc = deal.get("toc") or "Standard Escrow Protection Terms"
    toc = str(raw_toc)[:50]

    buyer_un = str(deal.get("buyer_un") or deal.get("buyer_id") or "Buyer")
    seller_un = str(deal.get("seller_un") or deal.get("seller_id") or "Seller")
    middleman_un = str(
        deal.get("middleman_un")
        or ("Assigned" if deal.get("middleman_id") else "Pending Assignment")
    )

    status_label = DEAL_STATUS_LABELS.get(status, status.replace("_", " ").title())
    time_taken = format_time_taken(deal)
    created_str = format_dt(deal.get("created_at"))

    # Timer
    timer_line = ""
    expires_at = deal.get("expires_at")
    if expires_at and status in ("awaiting_payment", "in_progress", "buyer_paid", "mm_connecting"):
        try:
            exp_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            rem = exp_dt - datetime.now(timezone.utc)
            total_mins = max(0, int(rem.total_seconds() / 60))
            hrs, mins = divmod(total_mins, 60)
            if hrs > 0:
                timer_line = f"\n⏰ Expires in: *{hrs}h {mins}m*"
            elif total_mins > 0:
                timer_line = f"\n⏰ Expires in: *{total_mins}m*"
            else:
                timer_line = "\n⏰ *Expiring soon\\!*"
        except Exception:
            pass

    text = (
        f"🤝 *VAULT\\-PAY ESCROW DEAL \\#{esc(deal_id)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *Product:* {esc(product_name)}\n"
        f"💰 *Amount:* ₹{amount:,}\n"
        f"📋 *ToS:* _{esc(toc)}_\n"
        f"🏦 *Fee \\(1%\\):* ₹{fee_amt:,} \\(Paid by: {esc(fee_payer.title())}\\)\n"
        f"👤 *Buyer:* @{esc(buyer_un)}\n"
        f"📦 *Seller:* @{esc(seller_un)}\n"
        f"🛡️ *Middleman:* @{esc(middleman_un)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Status:* *{esc(status_label)}*\n"
        f"⏱ *Time Taken:* *{esc(time_taken)}*\n"
        f"📅 *Created:* {esc(created_str)}"
    )
    text += timer_line

    if deal.get("released_at"):
        completed_str = format_dt(deal.get("released_at"))
        text += f"\n🏁 *Completed:* {esc(completed_str)}"
    elif deal.get("cancelled_at"):
        cancelled_str = format_dt(deal.get("cancelled_at"))
        text += f"\n❌ *Cancelled:* {esc(cancelled_str)}"

    if status == "completed":
        text += (
            f"\n\n🎉 *DEAL COMPLETED SUCCESSFULLY*\n"
            f"✅ All funds and deliverables settled safely via Vault\\-Pay\\."
        )
    elif status == "cancelled":
        text += (
            f"\n\n❌ *DEAL CANCELLED*\n"
            f"This escrow deal has been terminated\\."
        )
    elif status == "disputed":
        text += (
            f"\n\n🚨 *DEAL UNDER ACTIVE DISPUTE*\n"
            f"Deal frozen — Admin is reviewing the preserved room transcript\\."
        )
    else:
        text += (
            f"\n\n🔒 *Public Group Card: READ\\-ONLY*\n"
            f"⚡ All action buttons & chats are active in the registered *Private Deal Room*\\."
        )

    # All actionable buttons belong strictly in the registered private deal room
    return text, None


# ─── Deal finalization ─────────────────────────────────────────────────────────

async def finalize_deal(context, d: dict, group_msg):
    """Save deal to DB, edit group card, DM both parties."""
    try:
        if d.get("mm_type") == "auto":
            from auto_ltc_bot import create_draft
            await create_draft(context, d, group_msg)
            return
        fee_pct = await get_fee_pct()
        payment_method = "middleman"

        buyer_debit, seller_credit, fee_amt = compute_fee(
            d["amount"], fee_pct, d.get("fee_payer", "buyer")
        )

        if d["role"] == "buyer":
            buyer_id, buyer_un = d["initiator_id"], d["initiator_un"]
            seller_id, seller_un = d["counterparty_id"], d["counterparty_un"]
        else:
            seller_id, seller_un = d["initiator_id"], d["initiator_un"]
            buyer_id, buyer_un = d["counterparty_id"], d["counterparty_un"]

        deal_seq = await db.next_deal_id()
        deal_id = str(deal_seq)
        seller_pin = str(random.randint(1000, 9999))
        expires = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

        deal = {
            "deal_id": deal_id,
            "chat_id": d["chat_id"],
            "buyer_id": buyer_id,
            "buyer_un": buyer_un,
            "seller_id": seller_id,
            "seller_un": seller_un,
            "amount": d["amount"],
            "fee_payer": d.get("fee_payer", "buyer"),
            "fee_percent": fee_pct,
            "toc": str(d.get("toc") or "")[:50],
            "role": d["role"],
            "status": "awaiting_payment",
            "seller_pin": seller_pin,
            "message_id": group_msg.message_id if group_msg else None,
            "created_at": now_iso(),
            "expires_at": expires,
            "buyer_accepted": True,
            "seller_accepted": True,
            "product_name": str(d.get("product_name") or "")[:20],
            "payment_method": payment_method,
            "mm_type": d.get("mm_type", "manual"),
        }

        await db.create_deal(deal)
        await db.get_or_create_user(buyer_id, buyer_un)
        await db.get_or_create_user(seller_id, seller_un)

        # The original group card is the single source of truth for this flow.
        await publish_group_deal_card(context, deal, pin=True)
        await send_dm(
            buyer_id,
            f"✅ Deal \\#{esc(deal_id)} is active\\. Continue all deal actions from the pinned group card\\.",
        )
        await send_dm(
            seller_id,
            f"✅ Deal \\#{esc(deal_id)} is active\\. Continue all deal actions from the pinned group card\\.",
        )
        MM_STATE[deal_id] = {
            "status": "waiting_mm",
            "mm_id": None,
            "mm_un": None,
            "mm_upi": None,
            "seller_upi": None,
        }
        await notify_middlemen_for_deal(context, deal)

        # ── Identity Risk Check ──────────────────────────────────────────────────
        # High-value deals get a lightweight, non-blocking identity signal check on both
        # parties so the admin sees a red flag up front instead of finding out after a scam.
        risk_line = ""
        if d["amount"] >= RISK_CHECK_AMOUNT_THRESHOLD:
            try:
                import ai_judge
                risk_bits = []
                max_score = 0
                for pid, un in ((buyer_id, buyer_un), (seller_id, seller_un)):
                    try:
                        chat = await context.bot.get_chat(pid)
                    except Exception:
                        continue
                    score, notes = ai_judge.assess_identity_risk(chat, d["amount"], RISK_CHECK_AMOUNT_THRESHOLD)
                    max_score = max(max_score, score)
                    if notes:
                        risk_bits.append(f"@{un}: {notes} (score {score})")
                if risk_bits:
                    await db.update_deal(deal_id, risk_score=max_score, risk_notes="; ".join(risk_bits))
                    risk_line = "\n\n⚠️ *Identity Risk Check:*\n" + "\n".join(esc(b) for b in risk_bits)
                    if max_score >= 40:
                        for pid in (buyer_id, seller_id):
                            await send_dm(
                                pid,
                                f"🔎 *Extra caution advised — Deal \\#{esc(deal_id)}*\n"
                                "One or both accounts in this high\\-value deal look new/low\\-signal\\. "
                                "Please double\\-check identity/proofs carefully before paying or delivering\\.",
                            )
            except Exception as e:
                log.warning(f"identity risk check failed for deal {deal_id}: {e}")

        # Notify admin
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"🆕 *New Deal \\#{esc(deal_id)}*\n"
            f"Buyer: @{esc(buyer_un)} \\| Seller: @{esc(seller_un)}\n"
            f"Amount: ₹{d['amount']:,} \\| Via: {esc(payment_method)}"
            f"{risk_line}"
        )

        log.info(f"Deal {deal_id} finalized — buyer={buyer_id} seller={seller_id} method={payment_method}")

    except Exception as e:
        log.error(f"finalize_deal error: {e}", exc_info=True)
        if group_msg:
            await safe_edit(
                group_msg,
                "❌ *Failed to create deal*\\. Please try /escrow again\\."
            )


# ─── /escrow command ──────────────────────────────────────────────────────────

async def cmd_escrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    chat = update.effective_chat
    user = update.effective_user

    if await is_test_mode_admin_only() and user.id != TELEGRAM_ADMIN_ID:
        await msg.reply_text("🧪 Test mode is active. Only the admin account can start test deals.")
        return

    if chat.type == ChatType.PRIVATE:
        await msg.reply_text(
            "📢 Use /escrow in the official *escrow group* to start a deal\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Restrict deal creation to the official group ID
    if TELEGRAM_GROUP_ID and chat.id != TELEGRAM_GROUP_ID:
        await msg.reply_text(
            "⛔ Escrow deals can only be created in the official escrow group\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Check whitelisted
    if not await db.is_group_whitelisted(chat.id, TELEGRAM_GROUP_ID):
        await msg.reply_text("⛔ This group is not authorized for escrow\\.",
                             parse_mode=ParseMode.MARKDOWN_V2)
        return

    # Clear any existing draft for this user
    DRAFT.pop(user.id, None)

    default_toc = (await get_default_toc())[:50]
    if context.args and context.args[0].lower() == "ltc":
        if not auto_ltc.enabled():
            await msg.reply_text("The Litecoin regtest sandbox is disabled.")
            return
        other = msg.reply_to_message.from_user if msg.reply_to_message else None
        product = " ".join(context.args[1:]).strip()
        if not other or other.is_bot or other.id == user.id or not product:
            await msg.reply_text("Reply to the seller's message with /escrow ltc PRODUCT to open a private test deal.")
            return
        from auto_ltc_bot import create_draft
        public_card = await msg.reply_text("Reserving a private regtest deal room…")
        await create_draft(context, {"role": "buyer", "initiator_id": user.id,
            "counterparty_id": other.id, "product_name": product[:120], "amount": None,
            "fee_payer": "seller", "toc": "Seller delivers before buyer releases the test payment."}, public_card)
        return

    d = {
        "step": "role",
        "initiator_id": user.id,
        "initiator_un": user.username or str(user.id),
        "chat_id": chat.id,
        "msg_id": None,
        "role": None,
        "product_name": None,
        "amount": None,
        "fee_payer": None,
        "fee_pct": 1.0,
        "toc": None,
        "default_toc": default_toc,
        "payment_method": "middleman",
        "counterparty_id": None,
        "counterparty_un": None,
    }

    card_text, card_markup = build_draft_card(d)
    sent = await msg.reply_text(
        card_text, parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=card_markup,
    )
    d["msg_id"] = sent.message_id
    DRAFT[user.id] = d

    # Delete the /escrow command message
    await try_delete(msg)


# ─── Escrow callback handler ──────────────────────────────────────────────────

async def handle_escrow_cb(q, context, parts, user):
    action = parts[1] if len(parts) > 1 else ""

    # Cancel — works even without a draft
    if action == "cancel":
        DRAFT.pop(user.id, None)
        await safe_edit(q.message, "❌ *Deal creation cancelled\\.*")
        await qans(q)
        return

    # Accept/Reject counterparty — no draft needed on *this* user
    if action == "accept":
        initiator_id = int(parts[2]) if len(parts) > 2 else 0
        d = DRAFT.get(initiator_id)
        if not d:
            await qans(q, "⚠️ This deal invitation has expired.", alert=True)
            return
        # Must be the mentioned counterparty
        cp_un = d.get("counterparty_un", "").lower().lstrip("@")
        clicker_un = (user.username or "").lower()
        if not cp_un or clicker_un != cp_un or user.id == initiator_id:
            await qans(q, f"⛔ Only @{d['counterparty_un']} can accept this deal.", alert=True)
            return
        # Set counterparty ID now that we know it
        d["counterparty_id"] = user.id
        if not d.get("counterparty_un"):
            d["counterparty_un"] = user.username or str(user.id)
        # Delete the separate mention ping message
        if d.get("mention_msg_id") and d.get("chat_id"):
            try:
                await context.bot.delete_message(d["chat_id"], d["mention_msg_id"])
            except Exception:
                pass
        DRAFT.pop(initiator_id, None)
        await qans(q, "✅ Accepted! Creating deal...", alert=False)
        await finalize_deal(context, d, q.message)
        return

    if action == "reject":
        initiator_id = int(parts[2]) if len(parts) > 2 else 0
        d = DRAFT.pop(initiator_id, None)
        if d and d.get("mention_msg_id") and d.get("chat_id"):
            try:
                await context.bot.delete_message(d["chat_id"], d["mention_msg_id"])
            except Exception:
                pass
        rejecter = user.username or str(user.id)
        await safe_edit(
            q.message,
            f"❌ *Deal rejected by @{esc(rejecter)}\\.*\n_Run /escrow to start a new deal\\._"
        )
        await qans(q)
        return

    # All remaining actions require the initiator's draft
    d = DRAFT.get(user.id)
    if not d:
        await qans(q, "⛔ Only the deal creator can use these buttons.", alert=True)
        return

    # ── Role ──────────────────────────────────────────────────────────────────
    if action == "role":
        role = parts[2] if len(parts) > 2 else "buyer"
        d["role"] = role
        d["step"] = "product"  # NEW: go to product name step
        card_text, card_markup = build_draft_card(d)
        await safe_edit(q.message, card_text, card_markup)
        await qans(q)
        return

    # ── Fee payer ─────────────────────────────────────────────────────────────
    if action == "fee":
        fp = parts[2] if len(parts) > 2 else "buyer"
        d["fee_payer"] = fp
        d["step"] = "toc"
        card_text, card_markup = build_draft_card(d)
        await safe_edit(q.message, card_text, card_markup)
        await qans(q)
        return

    # ── ToS ───────────────────────────────────────────────────────────────────
    if action == "toc":
        choice = parts[2] if len(parts) > 2 else "skip"
        if choice == "default":
            d["toc"] = d.get("default_toc", "")
            d["step"] = "mm_mode"
            card_text, card_markup = build_draft_card(d)
            await safe_edit(q.message, card_text, card_markup)
        elif choice == "custom":
            CUSTOM_TOC_AWAIT[user.id] = True
            d["step"] = "custom_toc"
            card_text, card_markup = build_draft_card(d)
            await safe_edit(q.message, card_text, card_markup)
        else:  # skip
            d["toc"] = ""
            d["step"] = "mm_mode"
            card_text, card_markup = build_draft_card(d)
            await safe_edit(q.message, card_text, card_markup)
        await qans(q)
        return

    # ── Middleman Mode ────────────────────────────────────────────────────────
    if action == "mm_mode":
        choice = parts[2] if len(parts) > 2 else "manual"
        d["mm_type"] = choice
        d["payment_method"] = "middleman"
        d["step"] = "mention"
        card_text, card_markup = build_draft_card(d)
        await safe_edit(q.message, card_text, card_markup)
        await qans(q)
        return

    # ── Payment method ────────────────────────────────────────────────────────
    if action == "payment":
        d["payment_method"] = "middleman"
        d["step"] = "mention"
        card_text, card_markup = build_draft_card(d)
        await safe_edit(q.message, card_text, card_markup)
        await qans(q)
        return

    await qans(q)


# ─── Text handler (deal steps + wallet + relay) ───────────────────────────────

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    user = update.effective_user
    text = msg.text.strip()
    chat_type = update.effective_chat.type

    # ── Group: deal creation text steps ──────────────────────────────────────
    if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
        d = DRAFT.get(user.id)
        if not d:
            return

        step = d.get("step")

        # Product name input (NEW step 2)
        if step == "product":
            name = text.strip()
            if not name or len(name) < 2:
                await try_delete(msg)
                return
            d["product_name"] = name[:20]
            d["step"] = "amount"
            card_text, card_markup = build_draft_card(d)
            try:
                await context.bot.edit_message_text(
                    card_text, chat_id=d["chat_id"],
                    message_id=d["msg_id"],
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=card_markup,
                )
            except Exception as e:
                log.warning(f"edit msg: {e}")
            await try_delete(msg)
            return

        # Custom ToS text input
        if step == "custom_toc":
            toc_text = text.strip()[:50]
            if not toc_text:
                await try_delete(msg)
                return
            CUSTOM_TOC_AWAIT.pop(user.id, None)
            d["toc"] = toc_text
            d["step"] = "mm_mode"
            card_text, card_markup = build_draft_card(d)
            try:
                await context.bot.edit_message_text(
                    card_text, chat_id=d["chat_id"],
                    message_id=d["msg_id"],
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=card_markup,
                )
            except Exception as e:
                log.warning(f"custom_toc edit: {e}")
            await try_delete(msg)
            return

        # Amount input
        if step == "amount":
            raw = re.sub(r"[^\d]", "", text)
            if not raw:
                await try_delete(msg)
                err = await msg.reply_text("❌ Enter a valid amount \\(e\\.g\\. 500\\)\\.", parse_mode=ParseMode.MARKDOWN_V2)
                asyncio.create_task(_auto_delete(err))
                return
            amount_val = int(raw)
            if amount_val < 100:
                await try_delete(msg)
                err = await msg.reply_text("❌ Minimum amount is ₹100\\.", parse_mode=ParseMode.MARKDOWN_V2)
                asyncio.create_task(_auto_delete(err))
                return
            amount = amount_val
            if amount > 10_000_000:
                await try_delete(msg)
                err = await msg.reply_text("❌ Maximum amount is ₹1 crore\\.", parse_mode=ParseMode.MARKDOWN_V2)
                asyncio.create_task(_auto_delete(err))
                return
            d["amount"] = amount
            d["step"] = "fee"
            card_text, card_markup = build_draft_card(d)
            try:
                await context.bot.edit_message_text(
                    card_text, chat_id=d["chat_id"],
                    message_id=d["msg_id"],
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=card_markup,
                )
            except Exception as e:
                log.warning(f"edit msg: {e}")
            await try_delete(msg)
            return

        # @mention counterparty
        if step == "mention":
            # Extract @username from text
            match = re.search(r"@(\w+)", text)
            if not match:
                await try_delete(msg)
                err = await msg.reply_text("❌ Please @mention the counterparty\\.", parse_mode=ParseMode.MARKDOWN_V2)
                asyncio.create_task(_auto_delete(err))
                return
            cp_un = match.group(1)
            own_un = (user.username or "").lower()
            if cp_un.lower() == own_un:
                await try_delete(msg)
                err = await msg.reply_text("❌ You cannot mention yourself as counterparty\\.", parse_mode=ParseMode.MARKDOWN_V2)
                asyncio.create_task(_auto_delete(err))
                return

            d["counterparty_un"] = cp_un
            d["step"] = "waiting"
            card_text, card_markup = build_draft_card(d)
            try:
                await context.bot.edit_message_text(
                    card_text, chat_id=d["chat_id"],
                    message_id=d["msg_id"],
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=card_markup,
                )
            except Exception as e:
                log.warning(f"edit msg: {e}")
            await try_delete(msg)

            # Send a separate @mention ping message in the group (no parse_mode = real mention)
            try:
                ping_msg = await context.bot.send_message(
                    d["chat_id"],
                    f"👋 @{cp_un} — @{d['initiator_un']} wants to open an escrow deal with you!\n"
                    f"Amount: ₹{d['amount']:,} — tap ✅ I Accept on the card above 👆",
                )
                d["mention_msg_id"] = ping_msg.message_id
            except Exception as e:
                log.warning(f"mention ping: {e}")

            # DM counterparty if we know their ID
            cp_user = await db.get_user_by_username(cp_un)
            if cp_user:
                d["counterparty_id"] = cp_user["tg_id"]
                initiator_un = esc(d["initiator_un"])
                await send_dm(
                    cp_user["tg_id"],
                    f"🤝 *Deal Invitation from @{initiator_un}*\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"Amount: *₹{d['amount']:,}*\n\n"
                    f"💡 Go to the group and click *I Accept This Deal* button on the card\\.",
                )
            return

        return  # ignore other group text if no step matches

    # ── DM: wallet flow ───────────────────────────────────────────────────────
    if chat_type == ChatType.PRIVATE:
        # ── Middleman DM flow: UPI inputs ────────────────────────────────────
        if user.id in MM_DM_AWAIT:
            ctx = MM_DM_AWAIT[user.id]
            deal_id = ctx["deal_id"]
            role = ctx["role"]
            step_mm = ctx.get("step", "")

            # Middleman providing their UPI to buyer
            if role == "mm" and step_mm == "give_upi":
                upi = text.strip()
                if "@" not in upi or len(upi) < 5:
                    await msg.reply_text(
                        "❌ Invalid UPI\\. Format: `name@bank`",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    return
                MM_DM_AWAIT.pop(user.id, None)
                mm_st = MM_STATE.get(deal_id, {})
                mm_st["mm_upi"] = upi
                mm_st["status"] = "waiting_buyer_pay"
                MM_STATE[deal_id] = mm_st

                deal = await db.get_deal(deal_id)
                if not deal:
                    return

                await db.update_deal(deal_id, mm_upi=upi, status="waiting_buyer_pay")
                deal["mm_upi"] = upi
                deal["status"] = "waiting_buyer_pay"

                amount = deal["amount"]
                await send_or_update_room_flow(context, deal_id, stage="waiting_buyer_pay")
                await publish_group_deal_card(context, deal, pin=True)
                await send_dm(
                    deal["buyer_id"],
                    f"✅ Middleman payment details are ready for Deal #{deal_id}.\n"
                    f"Check the private deal room to view UPI details and confirm payment.",
                    parse_mode=None,
                )
                # DM middleman
                await msg.reply_text(
                    f"✅ *UPI details posted to private deal room\\!*\n"
                    f"⏳ Waiting for buyer to make payment and confirm\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                return

            # Seller providing their UPI to receive payment
            if role == "seller" and step_mm == "give_upi":
                upi = text.strip()
                if "@" not in upi or len(upi) < 5:
                    await msg.reply_text(
                        "❌ Invalid UPI\\. Format: `name@bank`",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    return
                MM_DM_AWAIT.pop(user.id, None)
                mm_st = MM_STATE.get(deal_id, {})
                mm_st["seller_upi"] = upi
                mm_st["status"] = "waiting_seller_pay"
                MM_STATE[deal_id] = mm_st

                deal = await db.get_deal(deal_id)
                if not deal:
                    return
                amount = deal["amount"]
                await db.update_deal(deal_id, seller_upi=upi)
                deal["seller_upi"] = upi

                await send_or_update_room_flow(context, deal_id, stage="waiting_seller_pay")
                await publish_group_deal_card(context, deal, pin=True)

                # DM middleman with seller UPI + button
                mm_id = mm_st.get("mm_id") or deal.get("middleman_id")
                if mm_id:
                    await send_dm(
                        mm_id,
                        f"📦 *Seller UPI for Deal \\#{esc(deal_id)}*\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"Seller: @{esc(deal['seller_un'])}\n"
                        f"UPI: `{esc(upi)}`\n"
                        f"Amount to send: *₹{amount:,}*\n\n"
                        f"Send the payment, then use the button in the private deal room to mark it paid.",
                    )
                await msg.reply_text(
                    f"✅ UPI submitted\\! Middleman is processing your payment of *₹{amount:,}*\\.\n"
                    f"You'll be notified when payment is sent\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                return

        # PIN release
        if user.id in PIN_AWAIT:
            deal_id = PIN_AWAIT[user.id]
            deal = await db.get_deal(deal_id)
            if not deal:
                PIN_AWAIT.pop(user.id, None)
                return
            if text == deal["seller_pin"]:
                PIN_AWAIT.pop(user.id, None)
                await complete_deal(context, deal, user)
            else:
                await msg.reply_text(
                    "❌ *Wrong PIN\\!* Try again or contact support\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            return

        # Vouch proof — user types "done" to finish submission
        if user.id in VOUCH_PROOF_AWAIT:
            if text.lower().strip() in ("done", "ok", "finish", "/done"):
                ctx = VOUCH_PROOF_AWAIT.pop(user.id, None)
                count = len(ctx.get("proofs", []))
                await msg.reply_text(
                    f"✅ *Proof submitted\\!* {count} screenshot\\(s\\) saved\\.\n"
                    f"Admin will review and approve your vouch\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await msg.reply_text(
                    "📸 *Send proof screenshots* \\(up to 3\\)\\.\n"
                    "Type *done* when finished\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            return

        # Dispute evidence — persistent text collection
        if user.id in EVIDENCE_AWAIT:
            ctx = EVIDENCE_AWAIT[user.id]
            deal_id = ctx["deal_id"]
            if text.lower().strip() in ("done", "finish", "/done"):
                EVIDENCE_AWAIT.pop(user.id, None)
                await msg.reply_text(
                    "✅ *Evidence package complete\\!* An admin will review your case manually\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                return
            is_abusive = await _check_abusive(user, text, deal_id, "dispute evidence")
            await db.add_dispute_message(deal_id, {
                "from": user.username or str(user.id),
                "type": "text", "text": text, "at": now_iso(),
                "flag": "abusive" if is_abusive else "",
            })
            await msg.reply_text(
                "📨 *Evidence saved\\!*\n"
                "Send more screenshots or text, or type *done* when finished\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            await send_dm(
                TELEGRAM_ADMIN_ID,
                f"📨 *Dispute Evidence \\(text\\)*\n"
                f"Deal: \\#{esc(deal_id)} \\| From: @{esc(user.username or str(user.id))}\n\n"
                f"{esc(text[:500])}"
            )
            return

        # Delayed dispute opening: the first message becomes the ticket's issue.
        if user.id in DISPUTE_AWAIT:
            deal_id = DISPUTE_AWAIT.pop(user.id, None)
            deal = await db.get_deal(deal_id)
            if not deal or user.id not in (deal.get("buyer_id"), deal.get("seller_id")):
                await msg.reply_text("That dispute request is no longer active.")
                return
            if deal.get("status") == "disputed":
                await msg.reply_text("A dispute is already open for this deal.")
                return
            if deal.get("status") in ("completed", "cancelled", "expired"):
                await msg.reply_text("This deal can no longer be disputed.")
                return
            await _check_abusive(user, text, deal_id, "dispute opening statement")
            ticket_code = await begin_manual_dispute(context, deal, user, text)
            await msg.reply_text(
                f"✅ Dispute ticket {ticket_code} is open.\n"
                "You can continue adding screenshots and text evidence here. "
                "Type 'done' when finished.",
                parse_mode=None,
            )
            return

        # Wallet flow
        if user.id in WALLET_STATE:
            await handle_wallet_text(update, context, text)
            return

        # Relay — buyer↔seller DM during in_progress deal
        if user.id in RELAY:
            r = RELAY[user.id]
            deal = await db.get_deal(r["deal_id"])
            if deal and deal["status"] in ("in_progress", "buyer_paid"):
                await _check_abusive(user, text, deal["deal_id"], "deal chat")
                await _auto_hold_guardian(deal, user, text)
                role_label = "Buyer" if r["role"] == "buyer" else "Seller"
                await send_dm(
                    r["other_id"],
                    f"💬 *{esc(role_label)} says:*\n{esc(text)}",
                )
            return


async def _auto_hold_guardian(deal: dict, user, text: str):
    """Auto-Hold Guardian: mid-deal, watch for scam signals (payment-method switch, pressure
    tactics, asks to hide things from admin) and autonomously pause fund release with a reasoned
    admin alert — instead of waiting for either party to raise a formal dispute."""
    if deal.get("on_hold"):
        return
    import ai_judge
    reason = ai_judge.scan_for_scam_signals(text)
    if not reason:
        return
    deal_id = deal["deal_id"]
    await db.update_deal(deal_id, on_hold=True, hold_reason=reason, held_at=now_iso())
    for pid in (deal["buyer_id"], deal["seller_id"]):
        await send_dm(
            pid,
            f"⏸️ *Deal \\#{esc(deal_id)} paused for a security review\\.*\n"
            "Our system flagged something in the chat that needs an admin's eyes before funds move\\. "
            "This is precautionary \\— no funds have been lost\\. Admin will follow up shortly\\.",
        )
    await send_dm(
        TELEGRAM_ADMIN_ID,
        f"🛑 *Auto\\-Hold Guardian triggered — Deal \\#{esc(deal_id)}*\n"
        f"Buyer: @{esc(deal['buyer_un'])} \\| Seller: @{esc(deal['seller_un'])} \\| Amount: ₹{deal['amount']:,}\n\n"
        f"*Reason:* {esc(reason)}\n"
        f"*Triggered by:* @{esc(user.username or str(user.id))}\n"
        f"*Message:* \"{esc(text[:300])}\"\n\n"
        "Funds are held \\(release/complete blocked\\) until you clear this hold\\.",
        markup=kb([[("✅ Clear Hold — Looks Fine", f"deal:hold_clear:{deal_id}")]]),
    )


# ─── Deal action handler ──────────────────────────────────────────────────────

async def complete_deal(context, deal: dict, pinner_user=None):
    """Release funds and mark deal completed."""
    deal_id = deal["deal_id"]
    amount = deal["amount"]
    fee_pct = deal.get("fee_percent", 1.0)
    fee_payer = deal.get("fee_payer", "buyer")
    buyer_debit, seller_credit, fee_amt = compute_fee(amount, fee_pct, fee_payer)

    # Release seller's VC (they get seller_credit)
    await db.adjust_balance(deal["seller_id"], delta_balance=seller_credit)
    # Unlock buyer's locked VC and deduct
    await db.adjust_balance(deal["buyer_id"], delta_locked=-buyer_debit)
    # Add fee to treasury
    await db.add_to_treasury(fee_amt)

    # Update deal stats via raw SQL increments
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET total_deals = total_deals + 1, total_volume = total_volume + $1 WHERE tg_id = $2",
            amount, deal["buyer_id"],
        )
        await conn.execute(
            "UPDATE users SET total_deals = total_deals + 1, total_volume = total_volume + $1 WHERE tg_id = $2",
            amount, deal["seller_id"],
        )

    await db.update_deal(deal_id,
                         status="completed",
                         released_at=now_iso())

    # Update tier
    for uid in (deal["buyer_id"], deal["seller_id"]):
        await update_tier(uid)

    # Edit group card
    deal["status"] = "completed"
    card_text, card_markup = build_deal_card(deal)
    pinned_msg_id = deal.get("message_id")
    try:
        if deal.get("chat_id") and pinned_msg_id:
            await context.bot.edit_message_text(
                card_text, chat_id=deal["chat_id"],
                message_id=pinned_msg_id,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=card_markup,
            )
    except Exception as e:
        log.warning(f"complete_deal edit: {e}")

    # Pin the group message
    try:
        if deal.get("chat_id") and pinned_msg_id:
            await context.bot.pin_chat_message(
                chat_id=deal["chat_id"],
                message_id=pinned_msg_id,
                disable_notification=True,
            )
    except Exception as e:
        log.warning(f"complete_deal pin: {e}")

    # Notify both parties
    await send_dm(deal["buyer_id"],
                  f"🎉 *Deal \\#{esc(deal_id)} Complete\\!*\n"
                  f"₹{amount:,} released to @{esc(deal['seller_un'])}\\. Thank you\\!")
    await send_dm(deal["seller_id"],
                  f"🎉 *Deal \\#{esc(deal_id)} Complete\\!*\n"
                  f"*{seller_credit:,} VC* added to your wallet\\! Thank you\\!")

    # Rating requests
    rating_markup = kb([
        [("⭐", f"rate:1:{deal_id}:{deal['seller_id']}"),
         ("⭐⭐", f"rate:2:{deal_id}:{deal['seller_id']}"),
         ("⭐⭐⭐", f"rate:3:{deal_id}:{deal['seller_id']}"),
         ("⭐⭐⭐⭐", f"rate:4:{deal_id}:{deal['seller_id']}"),
         ("⭐⭐⭐⭐⭐", f"rate:5:{deal_id}:{deal['seller_id']}")],
    ])
    await send_dm(deal["buyer_id"],
                  f"🌟 *Rate @{esc(deal['seller_un'])}* for deal \\#{esc(deal_id)}:",
                  markup=rating_markup)

    # Compute duration
    duration_str = ""
    try:
        c_dt = datetime.fromisoformat(str(deal.get("created_at", "")).replace("Z", "+00:00"))
        r_dt = datetime.now(timezone.utc)
        secs = int((r_dt - c_dt).total_seconds())
        if secs > 0:
            hrs, rem2 = divmod(secs, 3600)
            mins2, secs2 = divmod(rem2, 60)
            duration_str = f" \\| ⏱ {hrs}h {mins2}m" if hrs > 0 else f" \\| ⏱ {mins2}m {secs2}s"
    except Exception:
        pass

    # Post to channel
    if TELEGRAM_CHANNEL_ID:
        try:
            product_name = deal.get("product_name") or ""
            pm = "🛡️ Middleman" if deal.get("payment_method") == "middleman" else "💰 Wallet"
            prod_line = f"📦 Product: {esc(product_name)}\n" if product_name else ""
            await context.bot.send_message(
                TELEGRAM_CHANNEL_ID,
                f"✅ *Deal \\#{esc(deal_id)} Completed*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{prod_line}"
                f"💰 Amount: ₹{amount:,}\n"
                f"👤 Buyer: @{esc(deal['buyer_un'])}\n"
                f"📦 Seller: @{esc(deal['seller_un'])}\n"
                f"🏦 Fee: ₹{fee_amt:,} \\| Via: {esc(pm)}{duration_str}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            log.warning(f"channel post: {e}")

    # Clear relay
    RELAY.pop(deal["buyer_id"], None)
    RELAY.pop(deal["seller_id"], None)


async def update_tier(tg_id: int):
    u = await db.get_user(tg_id)
    if not u:
        return
    deals = u.get("total_deals", 0)
    if deals >= 50:
        tier = "elite"
    elif deals >= 20:
        tier = "trusted"
    elif deals >= 5:
        tier = "regular"
    else:
        tier = "new"
    if u.get("tier") != tier:
        await db.update_user(tg_id, tier=tier)


# ─── Middleman deal completion ──────────────────────────────────────────────────

async def complete_mm_deal(context, deal: dict):
    """Complete only after the room and group workflow have reached their final gates."""
    deal_id = deal["deal_id"]
    amount = deal["amount"]
    if deal.get("status") == "disputed":
        log.warning(f"blocked completion for disputed deal {deal_id}")
        return False
    if deal.get("room_chat_id") and not all(deal.get(key) for key in (
        "room_buyer_confirmed", "room_seller_confirmed", "room_middleman_confirmed",
    )):
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"⚠️ Completion blocked for Deal #{deal_id}: all three private-room confirmations "
            "are not recorded.",
            parse_mode=None,
        )
        return False

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET total_deals = total_deals + 1, total_volume = total_volume + $1 WHERE tg_id = $2",
            amount, deal["buyer_id"],
        )
        await conn.execute(
            "UPDATE users SET total_deals = total_deals + 1, total_volume = total_volume + $1 WHERE tg_id = $2",
            amount, deal["seller_id"],
        )

    released_ts = now_iso()
    await db.update_deal(deal_id, status="completed", released_at=released_ts)
    await record_room_event(
        deal_id, "deal_completed",
        user_id=TELEGRAM_ADMIN_ID,
        text="Deal completed after all room confirmations and final group-card workflow.",
    )

    for uid in (deal["buyer_id"], deal["seller_id"]):
        await update_tier(uid)

    deal["status"] = "completed"
    deal["released_at"] = released_ts
    await publish_group_deal_card(context, deal, pin=True)

    # Notify both parties
    await send_dm(deal["buyer_id"],
                  f"🎉 *Deal \\#{esc(deal_id)} Complete\\!*\n"
                  f"Seller received ₹{amount:,}\\. Thank you for using Vault\\-Pay\\!")
    await send_dm(deal["seller_id"],
                  f"🎉 *Deal \\#{esc(deal_id)} Complete\\!*\n"
                  f"₹{amount:,} paid to your UPI\\! Thank you for using Vault\\-Pay\\!")

    # Rating requests
    rating_markup = kb([
        [("⭐", f"rate:1:{deal_id}:{deal['seller_id']}"),
         ("⭐⭐", f"rate:2:{deal_id}:{deal['seller_id']}"),
         ("⭐⭐⭐", f"rate:3:{deal_id}:{deal['seller_id']}"),
         ("⭐⭐⭐⭐", f"rate:4:{deal_id}:{deal['seller_id']}"),
         ("⭐⭐⭐⭐⭐", f"rate:5:{deal_id}:{deal['seller_id']}")],
    ])
    await send_dm(deal["buyer_id"],
                  f"🌟 *Rate @{esc(deal['seller_un'])}* for deal \\#{esc(deal_id)}:",
                  markup=rating_markup)

    # Duration
    duration_str = ""
    try:
        c_dt = datetime.fromisoformat(str(deal.get("created_at", "")).replace("Z", "+00:00"))
        r_dt = datetime.fromisoformat(released_ts.replace("Z", "+00:00"))
        secs = int((r_dt - c_dt).total_seconds())
        if secs > 0:
            hrs, rem2 = divmod(secs, 3600)
            mins2, secs2 = divmod(rem2, 60)
            duration_str = f" \\| ⏱ {hrs}h {mins2}m" if hrs > 0 else f" \\| ⏱ {mins2}m {secs2}s"
    except Exception:
        pass

    # Post to channel
    if TELEGRAM_CHANNEL_ID:
        try:
            product_name = deal.get("product_name") or ""
            prod_line = f"📦 Product: {esc(product_name)}\n" if product_name else ""
            await context.bot.send_message(
                TELEGRAM_CHANNEL_ID,
                f"✅ *Deal \\#{esc(deal_id)} Completed via Middleman*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{prod_line}"
                f"💰 Amount: ₹{amount:,}\n"
                f"👤 Buyer: @{esc(deal['buyer_un'])}\n"
                f"📦 Seller: @{esc(deal['seller_un'])}\n"
                f"🛡️ Via: Middleman{duration_str}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            log.warning(f"mm channel post: {e}")

    # The private-room transcript is delivered by cleanup_middleman_room before the
    # room is scrubbed and returned to the pool.
    if deal.get("room_chat_id"):
        try:
            latest = await db.get_deal(deal_id)
            await cleanup_middleman_room(context, latest or deal)
        except Exception as e:
            log.warning(f"room transcript/cleanup failed for {deal_id}: {e}")

    MM_STATE.pop(deal_id, None)
    return True


# ─── Middleman callback handler ────────────────────────────────────────────────

async def handle_mm_cb(q, context, parts, user):
    """Handle mm: prefix callbacks for the middleman deal flow."""
    action = parts[1] if len(parts) > 1 else ""
    deal_id = parts[2] if len(parts) > 2 else ""

    deal = await db.get_deal(deal_id)
    if not deal:
        await qans(q, "Deal not found.", alert=True)
        return

    if (
        deal.get("payment_method") == "middleman"
        and q.message
        and q.message.chat_id == deal.get("chat_id")
        and q.message.chat_id != deal.get("room_chat_id")
    ):
        await qans(
            q,
            "This public deal card is read-only. Use the assigned private deal room.",
            alert=True,
        )
        return

    mm_st = MM_STATE.get(deal_id, {})
    if q.message and q.message.chat.type == ChatType.PRIVATE and action in {
        "buyer_paid", "mm_recv", "release", "release_ask", "release_confirm", "release_cancel", "release_no", "seller_connect",
        "paid_seller", "seller_recv_ask", "seller_recv", "seller_recv_no",
    }:
        target = "private deal room" if deal.get("room_chat_id") else "deal group"
        await qans(q, f"Use the action buttons in the {target} for this action.", alert=True)
        return

    # ── Auto-Hold Guardian: block fund-moving actions while on hold ─────────────
    if deal.get("on_hold") and action in (
        "buyer_paid", "mm_recv", "release", "release_ask", "release_confirm", "seller_connect",
        "paid_seller", "seller_recv_ask", "seller_recv",
    ):
        await qans(
            q,
            "⏸️ This deal is on hold for a security review. An admin will clear it shortly.",
            alert=True,
        )
        return

    # ── Middleman enters deal ──────────────────────────────────────────────────
    if action == "enter":
        if user.id != TELEGRAM_ADMIN_ID and not await db.is_middleman(user.id):
            await qans(q, "You are not an authorized middleman.", alert=True)
            return
        if mm_st.get("mm_id") or deal.get("middleman_id"):
            await qans(q, "⚠️ Another middleman already entered this deal.", alert=True)
            return
        if deal["status"] not in ("awaiting_payment", "mm_connecting"):
            await qans(q, "Deal is not in connecting stage.", alert=True)
            return

        mm_st["mm_id"] = user.id
        mm_st["mm_un"] = user.username or str(user.id)
        mm_st["status"] = "mm_entered"
        MM_STATE[deal_id] = mm_st
        await db.update_deal(
            deal_id,
            middleman_id=user.id,
            middleman_un=user.username or str(user.id),
        )
        deal["middleman_id"] = user.id
        deal["middleman_un"] = user.username or str(user.id)

        await qans(q, "✅ You entered the deal!", alert=True)
        await assign_middleman_room(
            context, deal, user.id, user.username or str(user.id)
        )
        MM_DM_AWAIT[user.id] = {"role": "mm", "deal_id": deal_id, "step": "give_upi"}
        try:
            await q.message.edit_text(
                f"✅ *You entered Deal \\#{esc(deal_id)}\\!*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📦 Product: {esc(deal.get('product_name') or 'N/A')}\n"
                f"💰 Amount: ₹{deal['amount']:,}\n"
                f"👤 Buyer: @{esc(deal['buyer_un'])}\n"
                f"📦 Seller: @{esc(deal['seller_un'])}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"*Send your UPI ID* to receive payment from buyer:",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception:
            pass
        await send_dm(
            deal["buyer_id"],
            f"✅ *Middleman Connected — Deal \\#{esc(deal_id)}*\n"
            f"Your middleman is @{esc(user.username or str(user.id))}\\.\n"
            f"⏳ They are sending you payment instructions\\.\\.\\.\n"
            f"You will also receive a private deal-room invite\\."
        )
        await send_dm(
            user.id,
            f"🛡️ *Verified Middleman Assigned — Deal \\#{esc(deal_id)}*\n"
            f"You are the designated room moderator for this deal\\.\n"
            f"Keep all parties inside the private room and use the pinned controls for confirmations, "
            f"disputes and closure\\.",
        )
        return

    # ── Middleman confirms payment received from buyer ─────────────────────────
    if action == "mm_recv":
        mm_id = mm_st.get("mm_id") or deal.get("middleman_id")
        if user.id != mm_id and user.id != TELEGRAM_ADMIN_ID:
            await qans(q, "Only the assigned middleman can do this.", alert=True)
            return

        mm_st["status"] = "payment_received"
        MM_STATE[deal_id] = mm_st
        await db.update_deal(deal_id, status="mm_payment_received")
        deal["status"] = "mm_payment_received"
        await send_or_update_room_flow(context, deal_id, stage="payment_received", q=q)
        await publish_group_deal_card(context, deal, pin=True)
        await refresh_middleman_room_card(context, deal_id)

        await qans(q, "✅ Confirmed! Deal room updated.", alert=True)

        await send_dm(
            deal["buyer_id"],
            f"✅ *Middleman confirmed your payment — Deal \\#{esc(deal_id)}*\n\n"
            f"Money is safe in escrow\\!\n"
            f"Go to the private deal room and click *🔓 Release Asset* after seller delivers\\."
        )
        await send_dm(
            deal["seller_id"],
            f"✅ *Buyer has paid — Deal \\#{esc(deal_id)}*\n\n"
            f"Payment is held safely by middleman\\.\n"
            f"⏳ Deliver your product/service to the buyer in the private room now\\."
        )
        return

    # ── Buyer says they paid ───────────────────────────────────────────────────
    if action == "buyer_paid":
        if user.id != deal["buyer_id"]:
            await qans(q, "Only the buyer can do this.", alert=True)
            return
        mm_id = mm_st.get("mm_id") or deal.get("middleman_id")
        mm_st["status"] = "buyer_paid_reported"
        MM_STATE[deal_id] = mm_st
        await db.update_deal(deal_id, status="buyer_paid")
        deal["status"] = "buyer_paid"
        await send_or_update_room_flow(context, deal_id, stage="buyer_paid_reported", q=q)
        await publish_group_deal_card(context, deal, pin=True)
        await refresh_middleman_room_card(context, deal_id)
        await qans(q, "⏳ Notifying middleman...", alert=False)
        if mm_id:
            await send_dm(
                mm_id,
                f"⚡ *Buyer says they paid — Deal \\#{esc(deal_id)}*\n"
                f"Check your account, then confirm using the button in the private deal room\\.",
            )
        return

    # ── Buyer asks to release asset (Double Confirmation Flow) ────────────────
    if action == "release_ask":
        if user.id != deal["buyer_id"]:
            await qans(q, "Only the buyer can release.", alert=True)
            return
        if deal["status"] != "mm_payment_received":
            await qans(q, "Cannot release at this stage.", alert=True)
            return
        await send_or_update_room_flow(context, deal_id, stage="release_ask", q=q)
        await qans(q, "Please confirm asset release.", alert=False)
        return

    # ── Buyer cancels release ──────────────────────────────────────────────────
    if action in ("release_no", "release_cancel"):
        if user.id != deal["buyer_id"]:
            await qans(q, "Only the buyer can cancel release.", alert=True)
            return
        await send_or_update_room_flow(context, deal_id, stage="payment_received", q=q)
        await qans(q, "Release cancelled. Deal continues.")
        return

    # ── Buyer confirms release ─────────────────────────────────────────────────
    if action in ("release", "release_confirm"):
        if user.id != deal["buyer_id"]:
            await qans(q, "Only the buyer can release.", alert=True)
            return
        if deal["status"] not in ("mm_payment_received", "buyer_paid"):
            await qans(q, "Cannot release at this stage.", alert=True)
            return

        await db.update_deal(deal_id, status="mm_asset_released")
        deal["status"] = "mm_asset_released"
        mm_st["status"] = "asset_released"
        MM_STATE[deal_id] = mm_st

        MM_DM_AWAIT[deal["seller_id"]] = {"role": "seller", "deal_id": deal_id, "step": "give_upi"}
        await send_or_update_room_flow(context, deal_id, stage="asset_released", q=q)
        await publish_group_deal_card(context, deal, pin=True)
        await refresh_middleman_room_card(context, deal_id)

        await qans(q, "✅ Asset released! Seller notified to give UPI.", alert=True)

        await send_dm(
            deal["seller_id"],
            f"🔓 *Buyer Released Asset — Deal \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Buyer confirmed delivery\\! You will receive ₹{deal['amount']:,}\\.\n\n"
            f"Reply here or directly in the private deal room with your UPI ID\\.",
        )
        mm_id = mm_st.get("mm_id") or deal.get("middleman_id")
        if mm_id:
            await send_dm(mm_id,
                          f"🔓 *Buyer released — Deal \\#{esc(deal_id)}*\n"
                          f"Seller @{esc(deal['seller_un'])} will connect to provide their UPI\\.")
        return

    # ── Seller connects to receive payment ─────────────────────────────────────
    if action == "seller_connect":
        if user.id != deal["seller_id"]:
            await qans(q, "Only the seller can do this.", alert=True)
            return
        if deal["status"] != "mm_asset_released":
            await qans(q, "Cannot connect at this stage.", alert=True)
            return

        MM_DM_AWAIT[user.id] = {"role": "seller", "deal_id": deal_id, "step": "give_upi"}
        await qans(q, "✅ Please type your UPI ID here or in the room.", alert=True)
        await send_dm(
            deal["seller_id"],
            f"✅ *Connected to Middleman — Deal \\#{esc(deal_id)}*\n\n"
            f"Type your UPI ID to receive ₹{deal['amount']:,}:",
        )
        mm_id = mm_st.get("mm_id") or deal.get("middleman_id")
        if mm_id:
            await send_dm(mm_id,
                          f"📦 *Seller connecting — Deal \\#{esc(deal_id)}*\n"
                          f"@{esc(deal['seller_un'])} is providing their UPI now\\.")
        return

    # ── Middleman confirms paid to seller ─────────────────────────────────────
    if action == "paid_seller":
        mm_id = mm_st.get("mm_id") or deal.get("middleman_id")
        if user.id != mm_id and user.id != TELEGRAM_ADMIN_ID:
            await qans(q, "Only the assigned middleman can do this.", alert=True)
            return

        await db.update_deal(deal_id, status="mm_seller_paid")
        deal["status"] = "mm_seller_paid"
        mm_st["status"] = "seller_paid"
        MM_STATE[deal_id] = mm_st

        await send_or_update_room_flow(context, deal_id, stage="seller_paid", q=q)
        await publish_group_deal_card(context, deal, pin=True)
        await refresh_middleman_room_card(context, deal_id)
        await qans(q, "✅ Marked as paid! Seller notified to confirm.", alert=True)
        await send_dm(
            deal["seller_id"],
            f"💸 *Middleman sent payment — Deal \\#{esc(deal_id)}*\n"
            f"Amount: ₹{deal['amount']:,}\n\n"
            f"Check your UPI account, then click the confirmation button in the private deal room\\.",
        )
        return

    # ── Seller asks to confirm receipt (Double Confirmation Flow) ─────────────
    if action == "seller_recv_ask":
        if user.id != deal["seller_id"]:
            await qans(q, "Only the seller can confirm.", alert=True)
            return
        await send_or_update_room_flow(context, deal_id, stage="seller_recv_ask", q=q)
        await qans(q, "Please confirm final receipt.", alert=False)
        return

    # ── Seller confirms received — deal complete! ─────────────────────────────
    if action == "seller_recv":
        if user.id != deal["seller_id"]:
            await qans(q, "Only the seller can confirm.", alert=True)
            return

        # Mark all room safety confirmations satisfied before completion
        deal["room_buyer_confirmed"] = True
        deal["room_seller_confirmed"] = True
        deal["room_middleman_confirmed"] = True
        await db.update_deal(
            deal_id,
            room_buyer_confirmed=True,
            room_seller_confirmed=True,
            room_middleman_confirmed=True,
        )

        completed = await complete_mm_deal(context, deal)
        if not completed:
            await qans(
                q,
                "Completion is blocked until the private-room safety checks are complete.",
                alert=True,
            )
            return
        await send_or_update_room_flow(context, deal_id, stage="completed", q=q)
        await publish_group_deal_card(context, deal, pin=False)
        await qans(q, "🎉 Deal completed!", alert=True)
        mm_id = mm_st.get("mm_id") or deal.get("middleman_id")
        if mm_id:
            await send_dm(mm_id,
                          f"🎉 *Deal \\#{esc(deal_id)} Completed\\!*\n"
                          f"Seller confirmed receipt\\. Deal closed\\. Well done\\!")
        return

    # ── Seller says not received ───────────────────────────────────────────────
    if action == "seller_recv_no":
        if user.id != deal["seller_id"]:
            await qans(q, "Only the seller can do this.", alert=True)
            return
        await send_or_update_room_flow(context, deal_id, stage="seller_paid", q=q)
        await qans(q, "Please raise a dispute if there is an issue.", alert=True)
        return

    await qans(q)


async def handle_room_cb(q, context, parts, user):
    """Controls shown on the pinned card inside a reusable private middleman room."""
    action = parts[1] if len(parts) > 1 else ""
    deal_id = parts[2] if len(parts) > 2 else ""
    deal = await db.get_deal(deal_id)
    if not deal or not deal.get("room_chat_id"):
        await qans(q, "Private deal room is not available.", alert=True)
        return
    if q.message and q.message.chat_id != deal.get("room_chat_id"):
        await qans(q, "This control belongs to another room.", alert=True)
        return

    role = _room_role_for_user(deal, user.id)
    if not role and user.id != TELEGRAM_ADMIN_ID:
        await qans(q, "You are not a participant in this deal room.", alert=True)
        return

    if action == "confirm":
        if deal.get("status") in ("disputed", "completed", "cancelled", "expired"):
            await qans(q, "This deal room is locked.", alert=True)
            return
        requested_role = parts[3] if len(parts) > 3 else ""
        if requested_role not in ("buyer", "seller", "middleman") or requested_role not in _room_roles_for_user(deal, user.id):
            await qans(q, "Only the assigned participant can use this confirmation.", alert=True)
            return
        flag = f"room_{requested_role}_confirmed"
        if deal.get(flag):
            await qans(q, "This confirmation is already recorded.", alert=True)
            return
        await db.update_deal(deal_id, **{flag: True})
        deal[flag] = True
        await record_room_event(
            deal_id, "participant_confirmed",
            user_id=user.id,
            username=user.username or str(user.id),
            role=requested_role,
            text=f"{requested_role.title()} confirmation recorded.",
        )
        await qans(q, f"✅ {requested_role.title()} confirmation recorded.", alert=True)
        card_text, card_markup = build_middleman_room_card(deal)
        await safe_edit(q.message, card_text, card_markup)
        all_confirmed = all(deal.get(key) for key in (
            "room_buyer_confirmed", "room_seller_confirmed", "room_middleman_confirmed",
        ))
        if all_confirmed and not deal.get("room_ready_to_proceed"):
            deal["room_ready_to_proceed"] = True
            deal["room_chat_locked"] = False
            await db.update_deal(
                deal_id, room_ready_to_proceed=True, room_chat_locked=False,
            )
            await set_middleman_room_permissions(context, deal, locked=False)
            ready_text, ready_markup = build_middleman_room_card(deal)
            await safe_edit(q.message, ready_text, ready_markup)
            await record_room_event(
                deal_id, "all_participants_confirmed",
                user_id=user.id,
                text="All participants confirmed. Room moved to protected proceed phase.",
            )
            try:
                notice = await context.bot.send_message(
                    deal["room_chat_id"],
                    "✅ ALL PARTICIPANTS CONFIRMED\n\n"
                    "The deal is ready to proceed. Use the next action button on this private-room card.\n\n"
                    "Buyer: follow the payment/release step shown in this room.\n"
                    "Seller: do not deliver or accept payment outside the recorded process.\n"
                    "Middleman: record every payment step and proof before moving forward.\n\n"
                    "The private room remains protected and all messages are preserved for admin review.",
                )
                await record_room_event(
                    deal_id, "proceed_guidance_posted",
                    message_id=notice.message_id,
                    text="Proceed guidance posted in the private room.",
                )
            except Exception as e:
                log.warning(f"proceed guidance failed for {deal_id}: {e}")
            for uid in (deal.get("buyer_id"), deal.get("seller_id"), deal.get("middleman_id")):
                if uid:
                    await send_dm(
                        uid,
                        f"✅ All participants confirmed Deal #{deal_id}.\n"
                        "Proceed only through the next action button in the private deal room.",
                        parse_mode=None,
                    )
            await publish_group_deal_card(context, deal, pin=True)
        return

    if action == "cancel":
        if role not in ("buyer", "seller", "middleman") and user.id != TELEGRAM_ADMIN_ID:
            await qans(q, "Only deal participants can cancel.", alert=True)
            return
        if deal.get("status") not in ("awaiting_payment", "waiting_buyer_pay", "mm_connecting"):
            await qans(q, "Cannot cancel after payment has been recorded.", alert=True)
            return
        c_ts = now_iso()
        await db.update_deal(deal_id, status="cancelled", cancelled_at=c_ts, cancelled=True)
        deal["status"] = "cancelled"
        deal["cancelled_at"] = c_ts
        await record_room_event(
            deal_id, "deal_cancelled",
            user_id=user.id,
            username=user.username or str(user.id),
            text=f"Deal cancelled from private room by {role.title() if role else 'Admin'}.",
        )
        await send_or_update_room_flow(context, deal_id, stage="cancelled", q=q)
        await publish_group_deal_card(context, deal, pin=False)
        card_text, card_markup = build_middleman_room_card(deal)
        await safe_edit(q.message, card_text, card_markup)
        await qans(q, "❌ Deal cancelled.", alert=True)
        for pid in (deal.get("buyer_id"), deal.get("seller_id"), deal.get("middleman_id")):
            if pid:
                await send_dm(pid, f"❌ Deal #{deal_id} was cancelled.")
        return

    if action == "dispute":
        if role not in ("buyer", "seller"):
            await qans(q, "Only buyer or seller can raise a dispute.", alert=True)
            return
        await send_or_update_room_flow(context, deal_id, stage="disputed", q=q)
        # Reuse the existing evidence intake, then restore the private-room card because
        # the generic deal handler edits whichever message contained the pressed button.
        await handle_deal_action(q, context, ["deal", "dispute", deal_id], user)
        latest = await db.get_deal(deal_id)
        if latest and q.message:
            card_text, card_markup = build_middleman_room_card(latest)
            await safe_edit(q.message, card_text, card_markup)
        return

    if action == "close":
        await qans(
            q,
            "Deals close automatically after the seller confirms receipt in this room.",
            alert=True,
        )
        return

    await qans(q)


async def handle_deal_action(q, context, parts, user):
    action = parts[1] if len(parts) > 1 else ""
    deal_id = parts[2] if len(parts) > 2 else ""

    deal = await db.get_deal(deal_id)
    if not deal:
        await qans(q, "Deal not found.", alert=True)
        return

    if action == "room_info":
        if user.id not in (
            deal.get("buyer_id"), deal.get("seller_id"),
            deal.get("middleman_id"), TELEGRAM_ADMIN_ID,
        ):
            await qans(q, "Only deal participants can view this instruction.", alert=True)
            return
        await qans(
            q,
            "This public card is status-only. Use the personal private-room invite "
            "to confirm terms and continue the deal.",
            alert=True,
        )
        await send_dm(
            user.id,
            f"🔐 Deal #{deal_id} — private room workflow\n\n"
            "The original group card is read-only.\n"
            "Use your private room invite for confirmations and the next recorded step.\n"
            "If you did not receive an invite, contact the admin.",
            parse_mode=None,
        )
        return

    if (
        deal.get("payment_method") == "middleman"
        and q.message
        and q.message.chat_id == deal.get("chat_id")
        and action != "room_info"
    ):
        await qans(
            q,
            "This public deal card is read-only. Use the assigned private deal room.",
            alert=True,
        )
        return

    # ── Auto-Hold Guardian: admin clears a hold ─────────────────────────────────
    if action == "hold_clear":
        if user.id != TELEGRAM_ADMIN_ID:
            await qans(q, "⛔ Admin only.", alert=True)
            return
        await db.update_deal(deal_id, on_hold=False, hold_reason="", held_at="")
        await qans(q, "✅ Hold cleared. Deal can continue.", alert=True)
        for pid in (deal["buyer_id"], deal["seller_id"]):
            await send_dm(
                pid,
                f"✅ *Deal \\#{esc(deal_id)} review complete\\.* You may continue \\— funds release is unblocked\\.",
            )
        return

    # ── Auto-Hold Guardian: block release/completion actions while on hold ──────
    if deal.get("on_hold") and action in (
        "wallet_release", "wallet_release_confirm", "paid",
    ):
        await qans(
            q,
            "⏸️ This deal is on hold for a security review. An admin will clear it shortly.",
            alert=True,
        )
        return

    # ── Pay Now → Send DM confirmation ────────────────────────────────────────
    if action == "pay":
        if user.id != deal["buyer_id"]:
            await qans(q, "⛔ Only the buyer can initiate payment.", alert=True)
            return
        if deal["status"] not in ("awaiting_payment", "mm_connecting"):
            await qans(q, "Deal is not in payment stage.", alert=True)
            return

        u = await db.get_or_create_user(user.id, user.username)
        if u.get("frozen"):
            await qans(q, "🧊 Your wallet is frozen. Contact admin.", alert=True)
            return

        fee_pct = deal.get("fee_percent", 1.0)
        buyer_debit, seller_credit, fee_amt = compute_fee(
            deal["amount"], fee_pct, deal.get("fee_payer", "buyer")
        )
        bal = u.get("balance_vc", 0)

        if bal < buyer_debit:
            shortfall = buyer_debit - bal
            await qans(q,
                       f"❌ Insufficient balance! Need {buyer_debit:,} VC, "
                       f"short by {shortfall:,} VC. Use /wallet to deposit.",
                       alert=True)
            return

        # Send DM confirmation — don't lock yet
        confirm_markup = kb([
            [("✅ Yes, Lock ₹{:,} VC".format(buyer_debit), f"deal:pay_confirm:{deal_id}")],
            [("❌ Cancel", f"deal:pay_cancel:{deal_id}")],
        ])
        await send_dm(
            user.id,
            f"💳 *Confirm Payment — Deal \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📦 Deal Amount: *₹{deal['amount']:,}*\n"
            f"🏦 Platform Fee: *₹{fee_amt:,}* \\({esc(deal.get('fee_payer','buyer'))} pays\\)\n"
            f"💸 *Total Deducted: {buyer_debit:,} VC*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💰 Your Balance: *{bal:,} VC*\n"
            f"After Pay: *{bal - buyer_debit:,} VC*\n\n"
            f"⚠️ Funds will be *held by bot* until you release\\.\n"
            f"Confirm to lock your funds in escrow:",
            markup=confirm_markup,
        )
        await qans(q, "✅ Check your DM to confirm payment!", alert=True)
        return

    # ── Pay Confirm (from DM) — lock wallet, activate relay ──────────────────
    if action == "pay_confirm":
        if user.id != deal["buyer_id"]:
            await qans(q, "⛔ Only the buyer can do this.", alert=True)
            return
        if deal["status"] != "awaiting_payment":
            await qans(q, "Deal is already in progress or completed.", alert=True)
            return

        u = await db.get_or_create_user(user.id, user.username)
        if u.get("frozen"):
            await qans(q, "🧊 Your wallet is frozen.", alert=True)
            return
        fee_pct = deal.get("fee_percent", 1.0)
        buyer_debit, seller_credit, fee_amt = compute_fee(
            deal["amount"], fee_pct, deal.get("fee_payer", "buyer")
        )
        if u.get("balance_vc", 0) < buyer_debit:
            await qans(q, "❌ Insufficient balance! Top up your wallet.", alert=True)
            return

        # Deduct from wallet and lock
        await db.adjust_balance(user.id, delta_balance=-buyer_debit, delta_locked=buyer_debit)
        await db.update_deal(deal_id, status="in_progress")

        # Update group card
        deal["status"] = "in_progress"
        try:
            if deal.get("chat_id") and deal.get("message_id"):
                card_text, card_markup = build_deal_card(deal)
                await context.bot.edit_message_text(
                    card_text, chat_id=deal["chat_id"],
                    message_id=deal["message_id"],
                    parse_mode=ParseMode.MARKDOWN_V2, reply_markup=card_markup,
                )
        except Exception as e:
            log.warning(f"pay_confirm card: {e}")

        # Set up relay for both parties
        RELAY[deal["buyer_id"]] = {
            "deal_id": deal_id, "other_id": deal["seller_id"],
            "other_un": deal["seller_un"], "role": "buyer",
        }
        RELAY[deal["seller_id"]] = {
            "deal_id": deal_id, "other_id": deal["buyer_id"],
            "other_un": deal["buyer_un"], "role": "seller",
        }

        # Confirm to buyer — edit their DM and give release button
        await safe_edit(
            q.message,
            f"✅ *Payment Locked — Deal \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💰 *{buyer_debit:,} VC* deducted and held securely by bot\\.\n\n"
            f"💬 *Secure Relay Chat Active\\!*\n"
            f"Text, images, files you send here in this DM go to the seller\\.\n\n"
            f"Once the seller delivers, click 🔓 Release Funds below:",
            markup=kb([
                [("🔓 Release Funds", f"deal:wallet_release:{deal_id}")],
                [("⚠️ Raise Dispute", f"deal:dispute:{deal_id}")],
            ]),
        )
        await qans(q, "✅ Payment locked! Chat with seller in this DM.")

        # Notify seller
        await send_dm(
            deal["seller_id"],
            f"✅ *Buyer Locked Payment — Deal \\#{esc(deal_id)}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Buyer @{esc(deal['buyer_un'])} locked *₹{deal['amount']:,}* in escrow\\.\n\n"
            f"💬 *Secure Relay Chat Active\\!*\n"
            f"You can text, send images and files here — buyer sees everything\\.\n\n"
            f"Deliver your product/service\\. Buyer will release funds when satisfied\\.",
            markup=kb([[("⚠️ Raise Dispute", f"deal:dispute:{deal_id}")]]),
        )
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"💰 *Wallet Payment Locked — Deal \\#{esc(deal_id)}*\n"
            f"Buyer: @{esc(deal['buyer_un'])} locked {buyer_debit:,} VC"
        )
        return

    # ── Pay Cancel (from DM) ──────────────────────────────────────────────────
    if action == "pay_cancel":
        await safe_edit(
            q.message,
            f"❌ *Payment cancelled\\.*\n\n"
            f"Go to the group and click *💳 Pay Now* when you're ready\\."
        )
        await qans(q, "Payment cancelled.")
        return

    # ── Wallet Release ─────────────────────────────────────────────────────────
    if action == "wallet_release":
        if user.id != deal["buyer_id"]:
            await qans(q, "⛔ Only the buyer can release funds.", alert=True)
            return
        if deal["status"] != "in_progress":
            await qans(q, "Cannot release at this stage.", alert=True)
            return
        fee_pct = deal.get("fee_percent", 1.0)
        buyer_debit, seller_credit, fee_amt = compute_fee(
            deal["amount"], fee_pct, deal.get("fee_payer", "buyer")
        )
        await safe_edit(
            q.message,
            f"🔓 *Confirm Fund Release — Deal \\#{esc(deal_id)}*\n\n"
            f"Releasing *₹{deal['amount']:,}* to @{esc(deal['seller_un'])}\\.\n"
            f"Seller receives: *₹{seller_credit:,}*\n\n"
            f"⚠️ *This cannot be undone\\.* Only confirm after receiving your product/service\\.",
            markup=kb([
                [("✅ Yes, Release Funds Now", f"deal:wallet_release_confirm:{deal_id}")],
                [("❌ No, Keep Holding", f"deal:wallet_release_cancel:{deal_id}")],
            ]),
        )
        await qans(q)
        return

    # ── Wallet Release Confirm ─────────────────────────────────────────────────
    if action == "wallet_release_confirm":
        if user.id != deal["buyer_id"]:
            await qans(q, "⛔ Only the buyer can release funds.", alert=True)
            return
        if deal["status"] != "in_progress":
            await qans(q, "Cannot release at this stage.", alert=True)
            return
        await safe_edit(q.message, "✅ *Releasing funds\\.\\.\\. Deal completing\\!*")
        await qans(q, "🎉 Funds released!")
        # Clean up relay
        RELAY.pop(deal["buyer_id"], None)
        RELAY.pop(deal["seller_id"], None)
        EVIDENCE_AWAIT.pop(deal["buyer_id"], None)
        EVIDENCE_AWAIT.pop(deal["seller_id"], None)
        await complete_deal(context, deal, user)
        return

    # ── Wallet Release Cancel ─────────────────────────────────────────────────
    if action == "wallet_release_cancel":
        await safe_edit(
            q.message,
            f"✅ *Funds still locked — Deal \\#{esc(deal_id)}*\n\n"
            f"Continue chatting with the seller\\.\n"
            f"Release when you're fully satisfied\\.",
            markup=kb([
                [("🔓 Release Funds", f"deal:wallet_release:{deal_id}")],
                [("⚠️ Raise Dispute", f"deal:dispute:{deal_id}")],
            ]),
        )
        await qans(q, "Holding funds. Chat more with seller.")
        return

    # ── Buyer says paid → enter PIN ───────────────────────────────────────────
    if action == "paid":
        if user.id != deal["buyer_id"]:
            await qans(q, "Only the buyer can do this.", alert=True)
            return
        if deal["status"] != "in_progress":
            await qans(q, "Invalid state.", alert=True)
            return

        await db.update_deal(deal_id, status="buyer_paid", paid_at=now_iso())
        deal["status"] = "buyer_paid"

        # Update group card
        try:
            if deal.get("chat_id") and deal.get("message_id"):
                card_text, card_markup = build_deal_card(deal)
                await context.bot.edit_message_text(
                    card_text, chat_id=deal["chat_id"],
                    message_id=deal["message_id"],
                    parse_mode=ParseMode.MARKDOWN_V2, reply_markup=card_markup,
                )
        except Exception as e:
            log.warning(f"paid card edit: {e}")

        # Ask for PIN
        PIN_AWAIT[user.id] = deal_id
        await qans(q, "Enter the PIN the seller gave you.", alert=True)
        await send_dm(
            deal["buyer_id"],
            f"🔑 *Enter the PIN* the seller shared with you:\n"
            f"_Type the 4\\-digit PIN in this chat_"
        )

        # Notify seller
        await send_dm(
            deal["seller_id"],
            f"✅ Buyer says they paid\\!\n"
            f"They are entering your PIN now\\. Once entered, funds will be released\\.\n"
            f"🔑 Your PIN: `{deal['seller_pin']}`"
        )
        return

    # ── Raise Dispute ─────────────────────────────────────────────────────────
    if action == "dispute":
        if user.id not in (deal["buyer_id"], deal["seller_id"]):
            await qans(q, "Only deal parties can raise a dispute.", alert=True)
            return
        pending_deal_id = DISPUTE_AWAIT.get(user.id)
        if pending_deal_id and str(pending_deal_id) != str(deal_id):
            await qans(q, "Finish describing your pending dispute first.", alert=True)
            await send_dm(
                user.id,
                f"You already have a pending dispute request for Deal #{pending_deal_id}. "
                "Send that issue description first, or use /start to reset the prompt.",
                parse_mode=None,
            )
            return
        if deal["status"] in ("completed", "cancelled", "expired"):
            await qans(q, "Cannot dispute a completed/cancelled deal.", alert=True)
            return
        if deal["status"] == "disputed":
            await qans(q, "⚠️ Dispute already open.", alert=True)
            return

        DISPUTE_AWAIT[user.id] = deal_id
        await qans(q, "Check your DM and describe the issue first.", alert=True)
        await send_dm(
            user.id,
            f"⚠️ Dispute request for Deal #{deal_id}\n\n"
            f"Please describe the issue in one message. The dispute ticket will open only after you submit it.\n"
            f"Include what happened, what outcome you want, and any important payment/delivery detail.",
            parse_mode=None,
        )
        return

    if action == "mm_wait":
        await qans(q, "The group card will update when the middleman is ready.", alert=True)
        return

    # ── Cancel Deal ───────────────────────────────────────────────────────────
    if action == "cancel":
        if user.id not in (deal["buyer_id"], deal["seller_id"]):
            await qans(q, "Only deal parties can cancel.", alert=True)
            return
        if deal["status"] not in ("awaiting_payment",):
            await qans(q, "Cannot cancel at this stage.", alert=True)
            return

        # Refund locked VC if any
        fee_pct = deal.get("fee_percent", 1.0)
        buyer_debit, _, _ = compute_fee(deal["amount"], fee_pct, deal.get("fee_payer", "buyer"))

        await db.update_deal(deal_id, status="cancelled")
        deal["status"] = "cancelled"
        card_text, card_markup = build_deal_card(deal)
        await safe_edit(q.message, card_text, card_markup)
        await qans(q, "Deal cancelled.")

        await send_dm(deal["buyer_id"],
                      f"❌ *Deal \\#{esc(deal_id)} cancelled\\.*")
        await send_dm(deal["seller_id"],
                      f"❌ *Deal \\#{esc(deal_id)} cancelled\\.*")
        return

    # ── Pay via Middleman ─────────────────────────────────────────────────────
    if action == "mm_pay":
        if user.id != deal["buyer_id"]:
            await qans(q, "Only the buyer can initiate payment.", alert=True)
            return
        if deal["status"] != "awaiting_payment":
            await qans(q, "Deal is not in payment stage.", alert=True)
            return

        u = await db.get_or_create_user(user.id, user.username)
        if u.get("frozen"):
            await qans(q, "🧊 Your wallet is frozen.", alert=True)
            return

        mm_st = MM_STATE.get(deal_id, {})
        # If a middleman already accepted from the deal-created notification, do not reset
        # that assignment or send a second race. The accepted middleman will provide payment
        # instructions in DM.
        if mm_st.get("mm_id") or deal.get("middleman_id"):
            await qans(q, "✅ Middleman already assigned. Check your DM for payment instructions.", alert=True)
            return

        mms = await db.get_active_middlemen()
        if not mms and not TELEGRAM_ADMIN_ID:
            await qans(q, "⚠️ No middlemen available now. Contact admin.", alert=True)
            return

        await db.update_deal(deal_id, status="mm_connecting")
        deal["status"] = "mm_connecting"
        card_text, card_markup = build_deal_card(deal)
        await safe_edit(q.message, card_text, card_markup)
        await qans(q, "Connecting to middleman... Check your DM!", alert=True)

        MM_STATE[deal_id] = {
            "status": "waiting_mm",
            "mm_id": None,
            "mm_un": None,
            "mm_upi": None,
            "seller_upi": None,
        }

        await send_dm(
            deal["buyer_id"],
            f"🔄 *Finding Middleman — Deal \\#{esc(deal_id)}*\n"
            f"Please wait\\. Our middlemen are being notified\\.\n"
            f"You'll receive further instructions shortly\\."
        )

        await notify_middlemen_for_deal(context, deal)
        return

    # ── Buyer asks to release asset ───────────────────────────────────────────
    if action == "mm_release_ask":
        if user.id != deal["buyer_id"]:
            await qans(q, "Only the buyer can release.", alert=True)
            return
        if deal["status"] != "mm_payment_received":
            await qans(q, "Cannot release at this stage.", alert=True)
            return
        confirm_markup = kb([
            [("✅ Yes, Release Asset", f"mm:release:{deal_id}")],
            [("❌ No, Wait", f"mm:release_no:{deal_id}")],
        ])
        await safe_edit(
            q.message,
            f"🔓 *Confirm Release — Deal \\#{esc(deal_id)}*\n\n"
            f"Seller: @{esc(deal['seller_un'])}\n"
            f"Amount: ₹{deal['amount']:,}\n\n"
            f"Are you sure you want to release the asset?",
            markup=confirm_markup,
        )
        await qans(q, "Confirm release in the group card.", alert=True)
        return

    await qans(q)


# ─── Wallet system ────────────────────────────────────────────────────────────

async def send_wallet_card(msg_or_q, u: dict, edit: bool = False):
    """Send or edit the wallet dashboard card."""
    badges = json.loads(u.get("badges", "[]") or "[]")
    badge_str = " ".join(badges) if badges else "None"
    rating_count = u.get("rating_count", 0)
    rating = (u.get("rating_sum", 0) / rating_count) if rating_count > 0 else 0.0

    TIER_MAP = {
        "new": "🌱 New", "regular": "⭐ Regular",
        "trusted": "💫 Trusted", "elite": "👑 Elite",
    }
    tier = TIER_MAP.get(u.get("tier", "new"), "🌱 New")

    text = (
        f"🏦 *VAULT\\-PAY WALLET*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 @{esc(u.get('username') or 'User')}  {esc(tier)}\n"
        f"🏅 Badges: {esc(badge_str)}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💳 *Balance: {u.get('balance_vc', 0):,} VC*\n"
        f"🔒 Locked in Deals: {u.get('locked_vc', 0):,} VC\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🤝 Total Deals: {u.get('total_deals', 0)}\n"
        f"💰 Total Volume: ₹{u.get('total_volume', 0):,}\n"
        f"⭐ Rating: {escn(rating, '.1f')}/5 \\({rating_count} reviews\\)\n"
        f"🏷️ Vouches: {u.get('vouches', 0)}"
    )
    markup = kb([
        [("➕ Deposit", "wal:deposit"), ("➖ Withdraw", "wal:withdraw")],
        [("📜 History", "wal:history"), ("🔗 Referral", "wal:referral")],
    ])
    if edit:
        await safe_edit(msg_or_q, text, markup)
    else:
        await msg_or_q.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2,
                                  reply_markup=markup)


async def cmd_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text(
            "💬 Use /wallet in *DM* with me\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    user = update.effective_user
    u = await db.get_or_create_user(user.id, user.username)
    WALLET_STATE.pop(user.id, None)  # clear any pending state
    await send_wallet_card(update.message, u, edit=False)


async def handle_wallet_cb(q, context, parts, user):
    action = parts[1] if len(parts) > 1 else ""

    if action == "back":
        WALLET_STATE.pop(user.id, None)
        u = await db.get_or_create_user(user.id, user.username)
        await send_wallet_card(q.message, u, edit=True)
        await qans(q)
        return

    if action == "deposit":
        WALLET_STATE[user.id] = {"step": "deposit_amount"}
        await safe_edit(
            q.message,
            "📥 *Deposit VC*\n\n"
            "1 VC = ₹1 \\(Indian Rupee\\)\n\n"
            f"Send payment to UPI: `{esc(ADMIN_UPI_ID)}`\n\n"
            "Type the amount you want to deposit \\(INR\\):\n"
            "_Minimum: ₹100_",
            kb([[("🔙 Back", "wal:back")]])
        )
        await qans(q)
        return

    if action == "withdraw":
        u = await db.get_or_create_user(user.id, user.username)
        bal = u.get("balance_vc", 0)
        if bal < 100:
            await qans(q, f"Minimum withdrawal is 100 VC. Your balance: {bal} VC.", alert=True)
            return
        WALLET_STATE[user.id] = {"step": "withdraw_amount", "balance": bal}
        await safe_edit(
            q.message,
            f"📤 *Withdraw VC*\n\n"
            f"Available: *{bal:,} VC*\n"
            f"Minimum: 100 VC\n\n"
            f"Type amount to withdraw \\(INR\\):",
            kb([[("🔙 Back", "wal:back")]])
        )
        await qans(q)
        return

    if action == "history":
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM transactions WHERE user_id = $1 ORDER BY at DESC LIMIT 10",
                user.id,
            )
        if not rows:
            await qans(q, "No transactions yet.", alert=True)
            return
        lines = []
        for r in rows:
            d = dict(r)
            sym = "📥" if d["type"] == "deposit" else "📤" if d["type"] == "withdrawal" else "💱"
            st = "✅" if d["status"] in ("approved", "paid", "completed") else "⏳" if d["status"] == "pending" else "❌"
            lines.append(f"{sym} {d['type'].title()} — {d['amount']:,} VC {st}")
        hist_text = "\n".join(lines)
        await safe_edit(
            q.message,
            f"📜 *Transaction History*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"{hist_text}",
            kb([[("🔙 Back", "wal:back")]])
        )
        await qans(q)
        return

    if action == "referral":
        u = await db.get_or_create_user(user.id, user.username)
        ref_code = u.get("referral_code") or f"REF{user.id}"
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM referrals WHERE referrer = $1", user.id
            ) or 0
        bot_username = (await context.bot.get_me()).username
        link = f"https://t.me/{bot_username}?start={ref_code}"
        await safe_edit(
            q.message,
            f"🔗 *Your Referral Link*\n\n"
            f"`{esc(link)}`\n\n"
            f"👥 Referrals: *{count}*\n"
            f"🎁 Earn *50 VC* for each friend who deposits\\!",
            kb([[("🔙 Back", "wal:back")]])
        )
        await qans(q)
        return

    await qans(q)


async def handle_wallet_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user = update.effective_user
    ws = WALLET_STATE.get(user.id, {})
    step = ws.get("step", "")

    # ── Deposit flow ──────────────────────────────────────────────────────────
    if step == "deposit_amount":
        raw = re.sub(r"[^\d]", "", text)
        if not raw or int(raw) < 100:
            await update.message.reply_text(
                "❌ Minimum deposit is ₹100\\. Try again:",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        amount = int(raw)
        ws["amount"] = amount
        ws["step"] = "deposit_screenshot"
        WALLET_STATE[user.id] = ws
        await update.message.reply_text(
            f"💳 *Deposit ₹{amount:,}*\n\n"
            f"1\\. Pay *₹{amount:,}* to UPI: `{esc(ADMIN_UPI_ID)}`\n"
            f"2\\. Take a screenshot of the payment\n"
            f"3\\. Send the screenshot here 👇",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb([[("❌ Cancel", "wal:back")]]),
        )
        return

    if step == "withdraw_amount":
        raw = re.sub(r"[^\d]", "", text)
        if not raw or int(raw) < 100:
            await update.message.reply_text(
                "❌ Minimum withdrawal is 100 VC\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        amount = int(raw)
        bal = ws.get("balance", 0)
        if amount > bal:
            await update.message.reply_text(
                f"❌ Insufficient balance\\! You have *{bal:,} VC*\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        ws["amount"] = amount
        ws["step"] = "withdraw_upi"
        WALLET_STATE[user.id] = ws
        await update.message.reply_text(
            f"📤 *Withdraw {amount:,} VC*\n\n"
            f"Enter your UPI ID to receive ₹{amount:,}:",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb([[("❌ Cancel", "wal:back")]]),
        )
        return

    if step == "withdraw_upi":
        upi = text.strip()
        if "@" not in upi or len(upi) < 5:
            await update.message.reply_text(
                "❌ Invalid UPI ID\\. Format: `name@bank`",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        ws["upi"] = upi
        ws["step"] = "withdraw_confirm"
        WALLET_STATE[user.id] = ws
        amount = ws["amount"]
        await update.message.reply_text(
            f"✅ *Confirm Withdrawal*\n\n"
            f"Amount: *{amount:,} VC* \\(₹{amount:,}\\)\n"
            f"UPI: `{esc(upi)}`\n\n"
            f"Confirm to submit request\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb([
                [("✅ Confirm", "wal:withdraw_confirm"),
                 ("❌ Cancel", "wal:back")],
            ]),
        )
        return


async def handle_wallet_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deposit screenshot photo in DM."""
    user = update.effective_user
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    ws = WALLET_STATE.get(user.id, {})
    if ws.get("step") != "deposit_screenshot":
        # State was lost or the user sent a photo out of order (e.g. skipped typing an amount) —
        # tell them instead of silently doing nothing, which used to look like a dead button.
        await update.message.reply_text(
            "🤔 I wasn't expecting a screenshot right now\\. Open /wallet → *Deposit* and enter "
            "an amount first, then send the payment screenshot\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document and (update.message.document.mime_type or "").startswith("image/"):
        file_id = update.message.document.file_id
    if not file_id:
        return

    amount = ws.get("amount", 0)
    u = await db.get_or_create_user(user.id, user.username)

    import uuid as _uuid
    tx_id = str(_uuid.uuid4())
    await db.create_transaction({
        "id": tx_id,
        "type": "deposit",
        "user_id": user.id,
        "amount": amount,
        "status": "pending",
        "data": {
            "username": user.username or str(user.id),
            "tg_id": user.id,
            "photo_file_id": file_id,
        },
        "at": now_iso(),
    })

    WALLET_STATE.pop(user.id, None)

    await update.message.reply_text(
        f"📥 *Deposit Request Submitted\\!*\n\n"
        f"Amount: *{amount:,} VC*\n"
        f"Status: 🤖 AI is verifying your screenshot\\.\\.\\.\n\n"
        f"This usually takes a few seconds\\. If the AI can't confirm it, admin will review manually\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb([[("🏦 Wallet", "wal:back")]]),
    )

    # Forward screenshot to admin
    await send_dm(
        TELEGRAM_ADMIN_ID,
        f"📥 *New Deposit Request*\n"
        f"User: @{esc(user.username)} \\(ID: {user.id}\\)\n"
        f"Amount: *{amount:,} VC*\n"
        f"TX: `{esc(tx_id[:12])}...`\n\n"
        f"Screenshot below 👇"
    )
    try:
        await context.bot.send_photo(TELEGRAM_ADMIN_ID, file_id,
                                     caption=f"Deposit proof — {amount} VC — @{user.username}")
    except Exception as e:
        log.warning(f"admin photo: {e}")

    # AI verifies the screenshot against the claimed amount — auto-credits on a clear match,
    # auto-rejects on a clear mismatch/fraud signal, or leaves it pending for manual review.
    asyncio.create_task(_run_deposit_ai_verify(tx_id))


async def _run_deposit_ai_verify(tx_id: str):
    try:
        import ai_judge
        await ai_judge.verify_deposit(tx_id)
    except Exception as e:
        log.error(f"deposit AI verify failed for {tx_id}: {e}", exc_info=True)


async def handle_withdraw_confirm_cb(q, context, user):
    ws = WALLET_STATE.get(user.id, {})
    if ws.get("step") != "withdraw_confirm":
        await qans(q)
        return

    amount = ws.get("amount", 0)
    upi = ws.get("upi", "")
    u = await db.get_or_create_user(user.id, user.username)

    if u.get("balance_vc", 0) < amount:
        await qans(q, "Insufficient balance!", alert=True)
        WALLET_STATE.pop(user.id, None)
        return

    # Deduct from balance
    await db.adjust_balance(user.id, delta_balance=-amount)

    import uuid as _uuid
    tx_id = str(_uuid.uuid4())
    await db.create_transaction({
        "id": tx_id,
        "type": "withdrawal",
        "user_id": user.id,
        "amount": amount,
        "status": "pending",
        "data": {
            "username": user.username or str(user.id),
            "tg_id": user.id,
            "address": upi,
        },
        "at": now_iso(),
    })

    WALLET_STATE.pop(user.id, None)
    u_updated = await db.get_or_create_user(user.id, user.username)
    await send_wallet_card(q.message, u_updated, edit=True)
    await qans(q, f"✅ Withdrawal of {amount} VC submitted!", alert=True)

    await send_dm(
        TELEGRAM_ADMIN_ID,
        f"📤 *Withdrawal Request*\n"
        f"User: @{esc(user.username)} \\(ID: {user.id}\\)\n"
        f"Amount: *{amount:,} VC*\n"
        f"UPI: `{esc(upi)}`\n"
        f"TX: `{esc(tx_id[:12])}...`"
    )


# ─── Rating handler ────────────────────────────────────────────────────────────

async def handle_rating_cb(q, context, parts, user):
    if len(parts) < 4:
        await qans(q)
        return
    stars = int(parts[1])
    deal_id = parts[2]
    target_id = int(parts[3])

    target = await db.get_user(target_id)
    if not target:
        await qans(q, "User not found.", alert=True)
        return

    new_sum = target.get("rating_sum", 0) + stars
    new_count = target.get("rating_count", 0) + 1
    await db.update_user(target_id, rating_sum=new_sum, rating_count=new_count)

    await safe_edit(
        q.message,
        f"⭐ *Thank you for rating\\!*\n"
        f"You gave @{esc(target.get('username', str(target_id)))} *{stars} star{'s' if stars > 1 else ''}*\\."
    )
    await qans(q, f"{'⭐' * stars} Rating submitted!", alert=True)


# ─── /rep & /profile ──────────────────────────────────────────────────────────

async def cmd_rep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not context.args:
        await msg.reply_text(
            "Usage: /rep @username",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    target_un = context.args[0].lstrip("@").strip()
    if not target_un:
        return

    u = await db.get_user_by_username(target_un)
    if not u:
        await msg.reply_text(
            f"❌ User @{esc(target_un)} not found\\. They must have used the bot\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    TIER_MAP = {
        "new": "🌱 New", "regular": "⭐ Regular",
        "trusted": "💫 Trusted", "elite": "👑 Elite",
    }
    tier = TIER_MAP.get(u.get("tier", "new"), "🌱 New")
    rating_count = u.get("rating_count", 0)
    rating = (u.get("rating_sum", 0) / rating_count) if rating_count > 0 else 0.0
    total_deals = u.get("total_deals", 0)
    total_volume = u.get("total_volume", 0)
    success_rate = "N/A"
    if total_deals > 0:
        success_rate = f"{min(100, int(total_deals * 100 / max(total_deals, 1)))}%"

    badges = json.loads(u.get("badges", "[]") or "[]")
    badge_str = " ".join(badges) if badges else "None"

    scam = "🔴 FLAGGED SCAMMER" if u.get("scam_flag") else "🟢 Clean"
    frozen = " \\| 🧊 FROZEN" if u.get("frozen") else ""

    # Vouches
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        vouch_rows = await conn.fetch(
            """SELECT v.text, u2.username as from_un
               FROM vouches v
               LEFT JOIN users u2 ON u2.tg_id = v.from_id
               WHERE v.to_id = $1
               ORDER BY v.at DESC LIMIT 5""",
            u["tg_id"],
        )

    vouch_lines = ""
    if vouch_rows:
        lines = [
            f"• _{esc(v['text'] or '—')}_ — @{esc(v['from_un'] or 'anon')}"
            for v in vouch_rows
        ]
        vouch_lines = "\n*Recent Vouches:*\n" + "\n".join(lines) + "\n"

    text = (
        f"🛡️ *TRUST PROFILE: @{esc(target_un)}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🏷️ Tier: {esc(tier)}\n"
        f"🏅 Badges: {esc(badge_str)}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🤝 Deals: {total_deals}\n"
        f"📈 Success Rate: {esc(success_rate)}\n"
        f"💰 Volume: ₹{total_volume:,}\n"
        f"⭐ Rating: {escn(rating, '.1f')}/5 \\({rating_count} reviews\\)\n"
        f"🏅 Vouches: {u.get('vouches', 0)}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🚨 Status: {esc(scam)}{frozen}"
        f"{vouch_lines}"
    )
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ─── /mydeals ─────────────────────────────────────────────────────────────────

async def cmd_mydeals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    deals = await db.get_user_deals(user.id)
    if not deals:
        await update.message.reply_text(
            "📋 You have no active deals\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    lines = []
    for d in deals[:5]:
        role = "Buyer" if d["buyer_id"] == user.id else "Seller"
        other_un = d["seller_un"] if role == "Buyer" else d["buyer_un"]
        lines.append(
            f"• Deal \\#{esc(d['deal_id'])} — ₹{d['amount']:,} — {esc(role)} "
            f"with @{esc(other_un)} — _{esc(d['status'])}_"
        )
    await update.message.reply_text(
        "📋 *Your Active Deals:*\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_transcript(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only PDF export for any preserved deal transcript."""
    user = update.effective_user
    if not user or user.id != TELEGRAM_ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /transcript DEAL_ID\nExample: /transcript 4092")
        return

    deal_id = context.args[0].lstrip("#").strip()
    deal = await db.get_deal(deal_id)
    if not deal:
        await update.message.reply_text(f"❌ Deal #{deal_id} not found.")
        return

    delivered = await send_room_transcript(deal, recipients=[user.id])
    if delivered:
        if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
            await update.message.reply_text("✅ Transcript PDF sent to your private chat.")
    else:
        await update.message.reply_text(
            "❌ Could not send the transcript PDF. Start a private chat with the bot and try again."
        )


async def cmd_register_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin or middleman command run inside each reusable private room."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        if update.message:
            await update.message.reply_text("Run /registerroom inside a private group room.")
        return

    if chat.id in auto_ltc.rooms():
        await update.message.reply_text("This room is reserved for Litecoin sandbox deals.")
        return

    # Authorized if TELEGRAM_ADMIN_ID, registered middleman, or administrator/creator of the group
    is_auth = (user.id == TELEGRAM_ADMIN_ID) or await db.is_middleman(user.id)
    if not is_auth:
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status in ("creator", "administrator"):
                is_auth = True
        except Exception as e:
            log.warning(f"Could not check member status for {user.id} in {chat.id}: {e}")

    if not is_auth:
        await update.message.reply_text("⛔ Only an Admin, Middleman, or Group Creator can register this room.")
        return

    try:
        bot_user = await context.bot.get_me()
        bot_member = await context.bot.get_chat_member(chat.id, bot_user.id)
        if bot_member.status not in ("administrator", "creator"):
            await update.message.reply_text("⚠️ Please make the bot an Administrator in this group first, then run /registerroom again.")
            return

        can_invite = getattr(bot_member, "can_invite_users", True)
        if not can_invite:
            await update.message.reply_text("⚠️ Please grant the bot 'Invite Users via Link' permission in group admin rights, then run /registerroom again.")
            return

        await db.register_middleman_room(chat.id, chat.title or str(chat.id))
        await db.set_middleman_room_status(chat.id, "available", "")
        await update.message.reply_text(
            f"✅ *Deal Room Successfully Registered\\!*\n\n"
            f"• Group: *{esc(chat.title or str(chat.id))}*\n"
            f"• Chat ID: `{chat.id}`\n"
            f"• Status: *Available for Deals*\n\n"
            f"This room is now permanently registered and will automatically recycle for future deals without needing /registerroom again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        log.warning(f"register room failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Could not register this room: {e}")


async def cmd_room_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View live statuses of all registered middleman rooms."""
    user = update.effective_user
    if not user:
        return
    is_auth = (user.id == TELEGRAM_ADMIN_ID) or await db.is_middleman(user.id)
    if not is_auth:
        await update.message.reply_text("⛔ Only Admins or Middlemen can view room statuses.")
        return
    rooms = await db.get_middleman_rooms()
    if not rooms:
        await update.message.reply_text("ℹ️ No middleman rooms registered yet. Run /registerroom inside a group room.")
        return
    lines = ["🏢 *Registered Escrow Rooms Live Status:*\n"]
    for idx, r in enumerate(rooms, 1):
        st = r.get("status", "available")
        badge = "🟢 Available" if st == "available" else ("🔴 Occupied" if st == "occupied" else "🟡 Quarantined")
        deal_info = f" (Deal #{r.get('deal_id')})" if r.get("deal_id") else ""
        lines.append(f"{idx}\\. *{esc(r.get('title') or 'Room')}* (`{r.get('chat_id')}`)\n   Status: *{badge}*{esc(deal_info)}")
    lines.append("\n_All registered rooms permanently remain in the pool and auto-refresh upon deal completion._")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_reset_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force reset the current room back to Available status."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return
    is_auth = (user.id == TELEGRAM_ADMIN_ID) or await db.is_middleman(user.id)
    if not is_auth:
        await update.message.reply_text("⛔ Only Admins or Middlemen can reset room status.")
        return
    await db.set_middleman_room_status(chat.id, "available", "")
    await update.message.reply_text(
        f"✅ Room `{chat.id}` has been successfully reset to *Available* for new deals\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ─── Storage Database Channel (/registerstorage, /setstorage, /storagestatus) ──

async def cmd_register_storage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command run directly inside the private storage/database channel to register it."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or user.id != TELEGRAM_ADMIN_ID:
        if update.message:
            await update.message.reply_text("⛔ Admin only command.")
        return

    if not chat or chat.type not in (ChatType.CHANNEL, ChatType.GROUP, ChatType.SUPERGROUP):
        if update.message:
            await update.message.reply_text("❌ Please run /registerstorage inside your private storage channel or group.")
        return

    try:
        bot_user = await context.bot.get_me()
        bot_member = await context.bot.get_chat_member(chat.id, bot_user.id)
        if bot_member.status not in ("administrator", "creator"):
            await update.message.reply_text("⚠️ Please make the bot an Administrator in this channel first with Post Messages and Delete Messages permissions.")
            return

        await db.set_media_storage_channel(chat.id)
        await update.message.reply_text(
            f"✅ *Private Media Storage Channel Successfully Registered\\!*\n\n"
            f"• Title: *{esc(chat.title or str(chat.id))}*\n"
            f"• Channel ID: `{chat.id}`\n"
            f"• Status: *Active Media Mirror*\n\n"
            f"🔒 All photos, videos, and documents sent in private deal rooms will now be automatically mirrored here with unique Reference IDs and auto\\-deleted after 24 hours for complete privacy\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        log.warning(f"register storage channel failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Failed to register storage channel: {e}")


async def cmd_set_storage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to set the storage channel ID manually: /setstorage <channel_id>"""
    user = update.effective_user
    if not user or user.id != TELEGRAM_ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    if not context.args:
        curr = await db.get_media_storage_channel()
        await update.message.reply_text(
            f"Current storage channel ID: `{curr}`\n\nUsage: `/setstorage <channel_id>`\nExample: `/setstorage -1001234567890`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw_id = context.args[0].strip()
    try:
        cid = int(raw_id)
        try:
            bot_user = await context.bot.get_me()
            bm = await context.bot.get_chat_member(cid, bot_user.id)
            if bm.status not in ("administrator", "creator"):
                await update.message.reply_text(f"⚠️ Note: Bot is not an admin in channel `{cid}` yet. Please promote the bot.")
        except Exception as ex:
            log.info(f"Could not check bot rights in {cid}: {ex}")

        await db.set_media_storage_channel(cid)
        await update.message.reply_text(
            f"✅ *Media Storage Channel set to:* `{cid}`\n\nAll deal photos, videos, and documents will be mirrored here with unique Ref IDs and 24h auto-purge.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except ValueError:
        await update.message.reply_text("❌ Invalid Channel ID. It should be an integer, e.g., -100xxxxxxxxxx")


async def cmd_storage_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to check storage channel status and pending auto-purges."""
    user = update.effective_user
    if not user or user.id != TELEGRAM_ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    cid = await db.get_media_storage_channel()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM pending_media_purges") or 0

    await update.message.reply_text(
        f"📦 *Vault\\-Pay Media Storage Status*\n\n"
        f"• *Storage Channel ID:* `{cid or 'Not Configured (0)'}`\n"
        f"• *Pending 24h Purges:* *{count}* files\n"
        f"• *Auto\\-Purge Scheduler:* Active \\(runs every 10 min\\)\n\n"
        f"To change: `/setstorage <channel_id>` or run `/registerstorage` inside the channel\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ─── /referral ────────────────────────────────────────────────────────────────

async def cmd_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await db.get_or_create_user(user.id, user.username)
    ref_code = u.get("referral_code") or f"REF{user.id}"
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer = $1", user.id
        ) or 0
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={ref_code}"
    await update.message.reply_text(
        f"🔗 *Your Referral Link*\n\n"
        f"`{esc(link)}`\n\n"
        f"👥 Total Referrals: *{count}*\n"
        f"🎁 Earn *50 VC* per referred user who deposits\\!",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = await db.get_or_create_user(user.id, user.username)

    # Handle referral code and deep links
    if context.args:
        ref_code = context.args[0]
        if ref_code.startswith("REF"):
            referrer_id_str = ref_code[3:]
            if referrer_id_str.isdigit() and int(referrer_id_str) != user.id:
                referrer_id = int(referrer_id_str)
                pool = await db.get_pool()
                async with pool.acquire() as conn:
                    existing = await conn.fetchrow(
                        "SELECT id FROM referrals WHERE referred = $1", user.id
                    )
                    if not existing:
                        import uuid as _uuid
                        await conn.execute(
                            "INSERT INTO referrals (id, referrer, referred, at) VALUES ($1,$2,$3,$4)",
                            str(_uuid.uuid4()), referrer_id, user.id, now_iso(),
                        )
                        await db.adjust_balance(referrer_id, delta_balance=50)
                        await send_dm(referrer_id,
                                      f"🎁 *Referral Bonus\\!* @{esc(user.username)} joined\\. "
                                      f"\\+50 VC added to your wallet\\!")

        # ── Relay secure chat deep link ────────────────────────────────────────
        elif ref_code.startswith("relay_"):
            if update.effective_chat.type != ChatType.PRIVATE:
                return
            deal_id = ref_code[6:]
            deal = await db.get_deal(deal_id)
            if not deal or deal.get("status") not in ("in_progress", "buyer_paid"):
                await update.message.reply_text(
                    "❌ Secure chat not available\\. Deal not found or not in progress\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                return
            if user.id == deal["buyer_id"]:
                role = "buyer"; other_id = deal["seller_id"]; other_un = deal["seller_un"]
            elif user.id == deal["seller_id"]:
                role = "seller"; other_id = deal["buyer_id"]; other_un = deal["buyer_un"]
            else:
                await update.message.reply_text(
                    "⛔ You are not part of this deal\\.", parse_mode=ParseMode.MARKDOWN_V2
                )
                return
            RELAY[user.id] = {"deal_id": deal_id, "other_id": other_id, "other_un": other_un, "role": role}
            action_btns = []
            if role == "buyer":
                action_btns.append([("🔓 Release Funds", f"deal:wallet_release:{deal_id}")])
            action_btns.append([("⚠️ Raise Dispute", f"deal:dispute:{deal_id}")])
            relay_hint = (
                "🔓 Click Release Funds when the seller has delivered\\."
                if role == "buyer"
                else "⏳ Deliver and wait for buyer to release funds\\."
            )
            await update.message.reply_text(
                f"💬 *Secure Deal Chat — \\#{esc(deal_id)}*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"You are the *{esc(role)}*\\. Chatting with @{esc(other_un)}\\.\n\n"
                f"Send text, photos, and files here — everything is relayed securely through the bot\\.\n\n"
                f"{relay_hint}",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=kb(action_btns),
            )
            return

        # ── Middleman deep link: buyer ────────────────────────────────────────
        elif ref_code.startswith("mm_B_"):
            if update.effective_chat.type != ChatType.PRIVATE:
                return
            deal_id = ref_code[5:]
            deal = await db.get_deal(deal_id)
            if not deal:
                await update.message.reply_text("❌ Deal not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return
            if user.id != deal["buyer_id"]:
                await update.message.reply_text("⛔ Only the buyer can use this link\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return
            mm_st = MM_STATE.get(deal_id, {})
            if mm_st.get("mm_upi"):
                buyer_markup = kb([[("✅ I've Paid", f"mm:buyer_paid:{deal_id}")],
                                   [("⚠️ Dispute", f"deal:dispute:{deal_id}")]])
                await update.message.reply_text(
                    f"💳 *Pay to Middleman — Deal \\#{esc(deal_id)}*\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"Amount: *₹{deal['amount']:,}*\n"
                    f"UPI: `{esc(mm_st['mm_upi'])}`\n\n"
                    f"Pay and click ✅ below\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=buyer_markup,
                )
            elif mm_st.get("mm_id"):
                await update.message.reply_text(
                    "⏳ Middleman connected\\. Awaiting payment instructions\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(
                    "🔄 Finding a middleman\\. Please wait\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            return

        # ── Middleman deep link: seller ───────────────────────────────────────
        elif ref_code.startswith("mm_S_"):
            if update.effective_chat.type != ChatType.PRIVATE:
                return
            deal_id = ref_code[5:]
            deal = await db.get_deal(deal_id)
            if not deal:
                await update.message.reply_text("❌ Deal not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return
            if user.id != deal["seller_id"]:
                await update.message.reply_text("⛔ Only the seller can use this link\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return
            if deal["status"] != "mm_asset_released":
                await update.message.reply_text("⚠️ Deal is not in the payment stage yet\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return
            MM_DM_AWAIT[user.id] = {"role": "seller", "deal_id": deal_id, "step": "give_upi"}
            await update.message.reply_text(
                f"✅ *Connected — Deal \\#{esc(deal_id)}*\n\n"
                f"Type your UPI ID to receive *₹{deal['amount']:,}*:",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            mm_st = MM_STATE.get(deal_id, {})
            mm_id = mm_st.get("mm_id")
            if mm_id:
                await send_dm(mm_id,
                              f"📦 *Seller connected — Deal \\#{esc(deal_id)}*\n"
                              f"@{esc(deal['seller_un'])} is providing their UPI\\.")
            return

    if update.effective_chat.type != ChatType.PRIVATE:
        return

    await update.message.reply_text(
        f"👋 Welcome to *Vault\\-Pay Escrow Bot*\\!\n\n"
        f"🤝 Secure peer\\-to\\-peer escrow for Indian traders\\.\n\n"
        f"*Commands:*\n"
        f"• /wallet — Your VC wallet\n"
        f"• /rep @user — Trust profile\n"
        f"• /mydeals — Active deals\n"
        f"• /referral — Earn rewards\n\n"
        f"📢 Use /escrow in a *group* to start a deal\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ─── +vouch handler ───────────────────────────────────────────────────────────

async def handle_vouch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle +vouch @user text in groups — creates pending vouch awaiting admin approval."""
    msg = update.message
    if not msg or not msg.text:
        return
    text = msg.text.strip()
    if not text.lower().startswith("+vouch"):
        return
    parts = text.split(None, 2)
    if len(parts) < 3:
        await msg.reply_text(
            "Usage: `\\+vouch @username Your reason for vouching`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    target_un = parts[1].lstrip("@")
    vouch_text = parts[2].strip()
    if not vouch_text:
        return
    user = update.effective_user

    target = await db.get_user_by_username(target_un)
    if not target:
        await msg.reply_text(
            f"❌ @{esc(target_un)} not found\\. They must start the bot first\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    if target["tg_id"] == user.id:
        await msg.reply_text("❌ You cannot vouch for yourself\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, status FROM vouches WHERE from_id = $1 AND to_id = $2",
            user.id, target["tg_id"],
        )
        if existing:
            status = existing["status"]
            if status == "pending":
                await msg.reply_text(
                    f"⏳ Your vouch for @{esc(target_un)} is already *pending* approval\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await msg.reply_text(
                    f"✅ You have already vouched for @{esc(target_un)}\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            return
        import uuid as _uuid
        vouch_id = str(_uuid.uuid4())
        await conn.execute(
            """INSERT INTO vouches (id, from_id, to_id, text, at, status, proof_json)
               VALUES ($1,$2,$3,$4,$5,'pending','[]')""",
            vouch_id, user.id, target["tg_id"], vouch_text, now_iso(),
        )

    # Confirm to voucher with "Add Proof" button
    await msg.reply_text(
        f"⏳ *Vouch submitted for @{esc(target_un)}\\!*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Status: *Pending admin approval*\n\n"
        f"📸 *Boost approval:* Add proof screenshots \\(chat history, reviews, etc\\)\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb([[("📸 Add Proof Screenshots", f"vouch:add_proof:{vouch_id}")]]),
    )

    # Notify admin with approve/reject buttons
    from telegram import InlineKeyboardButton as IKB
    admin_markup = InlineKeyboardMarkup([
        [IKB("✅ Approve Vouch", callback_data=f"admin_vouch:approve:{vouch_id}"),
         IKB("❌ Reject", callback_data=f"admin_vouch:reject:{vouch_id}")],
        [IKB("💬 DM Voucher", url=f"tg://user?id={user.id}"),
         IKB("💬 DM Recipient", url=f"tg://user?id={target['tg_id']}")],
    ])
    if _bot_app:
        try:
            await _bot_app.bot.send_message(
                TELEGRAM_ADMIN_ID,
                f"🤝 *New Vouch Pending*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"From: @{esc(user.username or str(user.id))}\n"
                f"To: @{esc(target_un)}\n\n"
                f"📝 _{esc(vouch_text[:300])}_\n\n"
                f"📸 No proof yet \\(voucher can add via button\\)\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=admin_markup,
            )
        except Exception as e:
            log.warning(f"vouch admin notify: {e}")


# ─── Vouch callbacks (vouch:add_proof) ───────────────────────────────────────

async def handle_vouch_cb(q, context, parts, user):
    action = parts[1] if len(parts) > 1 else ""
    vouch_id = parts[2] if len(parts) > 2 else ""
    if action == "add_proof":
        vouch = await db.get_vouch_by_id(vouch_id)
        if not vouch:
            await qans(q, "❌ Vouch not found.", alert=True)
            return
        if vouch["from_id"] != user.id:
            await qans(q, "⛔ This is not your vouch.", alert=True)
            return
        if vouch["status"] != "pending":
            await qans(q, f"This vouch is already {vouch.get('status','processed')}.", alert=True)
            return
        VOUCH_PROOF_AWAIT[user.id] = {"vouch_id": vouch_id, "proofs": []}
        await send_dm(
            user.id,
            f"📸 *Submit Proof Screenshots*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Send up to *3 screenshots* \\(chat history, reviews, transaction records\\)\\.\n"
            f"Type *done* when finished\\.",
        )
        await qans(q, "✅ Check your DM to submit proof screenshots!", alert=True)
    else:
        await qans(q)


async def handle_admin_vouch_cb(q, context, parts, user):
    action = parts[1] if len(parts) > 1 else ""
    vouch_id = parts[2] if len(parts) > 2 else ""
    if action == "approve":
        v = await db.approve_vouch(vouch_id)
        if not v:
            await qans(q, "❌ Vouch not found or already processed.", alert=True)
            return
        orig_text = q.message.text or ""
        await safe_edit(q.message, f"✅ *Vouch APPROVED*\n\n{esc(orig_text[:300])}")
        await qans(q, "✅ Vouch approved!")
        if v.get("to_id"):
            to_un = v.get("to_un") or str(v.get("to_id", ""))
            from_un = v.get("from_un") or str(v.get("from_id", ""))
            await send_dm(
                v["to_id"],
                f"🎉 *Vouch Approved\\!*\n"
                f"A verified vouch from @{esc(from_un)} has been added to your profile\\.\n"
                f"Your trust score has been updated\\!",
            )
        if v.get("from_id"):
            to_un = v.get("to_un") or str(v.get("to_id", ""))
            await send_dm(
                v["from_id"],
                f"✅ *Your vouch for @{esc(to_un)} was approved by admin\\!*",
            )
    elif action == "reject":
        v = await db.reject_vouch(vouch_id)
        if not v:
            await qans(q, "❌ Vouch not found or already processed.", alert=True)
            return
        orig_text = q.message.text or ""
        await safe_edit(q.message, f"❌ *Vouch REJECTED*\n\n{esc(orig_text[:300])}")
        await qans(q, "❌ Vouch rejected.")
        if v.get("from_id"):
            to_un = v.get("to_un") or str(v.get("to_id", ""))
            await send_dm(
                v["from_id"],
                f"❌ *Vouch Rejected*\n"
                f"Your vouch for @{esc(to_un)} was not approved\\.\n"
                f"Submit stronger proof if you believe it's valid\\.",
            )
    else:
        await qans(q)


# ─── DM photo router ──────────────────────────────────────────────────────────

async def handle_dm_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route photos in DM: relay → vouch proof → dispute evidence → wallet deposit.

    Also handles screenshots sent as an uncompressed "file"/document (image/*) — a very common
    case on Telegram Desktop (drag-and-drop or "send without compression"). Without this, those
    screenshots arrived as a `document` update, which the old photo-only handler never saw at
    all — evidence/deposit proof looked like it vanished into thin air."""
    msg = update.message
    if not msg:
        return
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        file_id = msg.document.file_id
    if not file_id:
        return
    user = update.effective_user

    # NOTE: check order here must mirror the text handler's priority (PIN → vouch proof →
    # dispute evidence → wallet → relay). A dispute doesn't clear the stale RELAY entry (both
    # parties stay in RELAY until the deal completes/cancels), so if RELAY were checked first,
    # every screenshot sent after a dispute opens would get silently swallowed here instead of
    # ever reaching EVIDENCE_AWAIT below — which is exactly what was happening.

    # Vouch proof screenshots
    if user.id in VOUCH_PROOF_AWAIT:
        ctx = VOUCH_PROOF_AWAIT[user.id]
        proofs = ctx.get("proofs", [])
        if len(proofs) >= 3:
            await msg.reply_text(
                "❌ Max 3 screenshots reached\\. Type *done* to submit\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        proofs.append(file_id)
        ctx["proofs"] = proofs
        import json as _j
        await db.update_vouch_proof(ctx["vouch_id"], _j.dumps(proofs))
        remaining = 3 - len(proofs)
        if remaining > 0:
            await msg.reply_text(
                f"✅ Screenshot {len(proofs)}/3 saved\\. {remaining} slot\\(s\\) left\\. Send more or type *done*\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        else:
            VOUCH_PROOF_AWAIT.pop(user.id, None)
            await msg.reply_text(
                "✅ *All 3 screenshots saved\\!* Proof submitted — admin will review\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        if _bot_app:
            try:
                await context.bot.copy_message(
                    chat_id=TELEGRAM_ADMIN_ID, from_chat_id=msg.chat_id, message_id=msg.message_id,
                    caption=f"📸 Vouch proof [{len(proofs)}/3] — {ctx['vouch_id'][:8]} | @{user.username or user.id}",
                )
            except Exception as e:
                log.warning(f"vouch proof admin: {e}")
        return

    # Dispute evidence screenshots
    if user.id in EVIDENCE_AWAIT:
        ctx = EVIDENCE_AWAIT[user.id]
        deal_id = ctx["deal_id"]
        ctx["count"] = ctx.get("count", 0) + 1
        caption_for_check = (msg.caption or "").strip()
        is_abusive = await _check_abusive(user, caption_for_check, deal_id, "dispute evidence photo caption") if caption_for_check else False
        await db.add_dispute_message(deal_id, {
            "from": user.username or str(user.id),
            "type": "photo", "file_id": file_id, "at": now_iso(),
            "flag": "abusive" if is_abusive else "",
        })
        if _bot_app:
            try:
                await context.bot.copy_message(
                    chat_id=TELEGRAM_ADMIN_ID, from_chat_id=msg.chat_id, message_id=msg.message_id,
                    caption=f"🚨 Dispute evidence — Deal #{deal_id} | @{user.username or user.id}",
                )
            except Exception as e:
                log.warning(f"evidence photo admin: {e}")

        # A photo's caption can carry "done" (common on mobile: attach caption, then send) —
        # without this check, evidence sent with a "done" caption silently never finished.
        caption = (msg.caption or "").lower().strip()
        if caption in ("done", "finish", "/done"):
            EVIDENCE_AWAIT.pop(user.id, None)
            await msg.reply_text(
                "✅ *Evidence package complete\\!* An admin will review your case manually\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        else:
            await msg.reply_text(
                f"📸 *Screenshot \\#{ctx['count']} saved* as evidence\\.\n"
                f"Send more or type *done* when finished\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        return

    # Relay: forward photo to other party (buyer↔seller DM during an in_progress deal).
    # Checked AFTER vouch-proof/dispute-evidence above, mirroring the text handler's priority —
    # a still-open RELAY entry must never swallow a screenshot meant for evidence/wallet flows.
    if user.id in RELAY:
        r = RELAY[user.id]
        deal = await db.get_deal(r["deal_id"])
        if deal and deal["status"] in ("in_progress", "buyer_paid"):
            role_label = "Buyer" if r["role"] == "buyer" else "Seller"
            try:
                # copy_message preserves whichever type it actually is (compressed photo or
                # uncompressed image document) — re-sending a document's file_id via send_photo
                # would fail, since Telegram ties file_id to its original upload type.
                await context.bot.copy_message(
                    chat_id=r["other_id"], from_chat_id=msg.chat_id, message_id=msg.message_id,
                    caption=f"📷 {role_label} sent a photo",
                )
            except Exception as e:
                log.warning(f"relay photo: {e}")
        return

    # Default: wallet deposit screenshot
    await handle_wallet_photo(update, context)


# ─── Auto-nudge stale deals ───────────────────────────────────────────────────

async def nudge_stale_deals():
    """DM parties on deals stale >4h and not nudged in last 12h."""
    from datetime import datetime, timezone, timedelta
    try:
        pool = await db.get_pool()
        stale_cut = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        nudge_cut = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM deals
                   WHERE status IN ('awaiting_payment','in_progress','buyer_paid','mm_connecting')
                   AND created_at < $1
                   AND (nudged_at IS NULL OR nudged_at < $2)
                   LIMIT 50""",
                stale_cut, nudge_cut,
            )
        messages = {
            "awaiting_payment": "💳 Payment hasn't been made yet\\. Go to the group and click *Pay Now*\\.",
            "in_progress": "🔄 Deal in progress\\. Seller: deliver\\. Buyer: release funds when satisfied\\.",
            "buyer_paid": "✅ Buyer marked paid\\. Seller: confirm receipt\\.",
            "mm_connecting": "🤝 Middleman connection pending\\. Contact admin if stuck\\.",
        }
        for r in rows:
            deal = dict(r)
            did = deal["deal_id"]
            if did in NUDGED:
                continue
            NUDGED.add(did)
            nudge = messages.get(deal.get("status", ""), "⏰ Your deal needs attention\\.")
            for uid in filter(None, [deal.get("buyer_id"), deal.get("seller_id")]):
                await send_dm(
                    uid,
                    f"⏰ *Deal \\#{esc(did)} — Reminder*\n\n{nudge}\n\nReturn to the group to continue\\."
                )
            pool2 = await db.get_pool()
            async with pool2.acquire() as conn2:
                await conn2.execute(
                    "UPDATE deals SET nudged_at = $1 WHERE deal_id = $2", now_iso(), did
                )
            log.info(f"Nudged deal {did}")
    except Exception as e:
        log.error(f"nudge_stale_deals: {e}", exc_info=True)


# ─── Auto-whitelist on bot added to group ────────────────────────────────────

async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return
    me = await context.bot.get_me()
    chat = update.effective_chat
    for member in update.message.new_chat_members:
        if member.id == me.id:
            await db.add_group(chat.id, chat.title or str(chat.id), "auto")
            await update.message.reply_text(
                "✅ *Vault\\-Pay Escrow* is now active in this group\\!\n"
                "Use /escrow to start a deal\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            log.info(f"Bot added to group {chat.id} — auto-whitelisted")

    # Reusable private middleman room: only the three assigned Telegram IDs are allowed.
    room = await db.get_middleman_room(chat.id)
    if not room or not room.get("deal_id"):
        return
    deal = await db.get_deal(room["deal_id"])
    if not deal:
        return
    expected = {deal.get("buyer_id"), deal.get("seller_id"), deal.get("middleman_id")}
    for member in update.message.new_chat_members:
        if member.id == me.id:
            continue
        if member.id not in expected and member.id != TELEGRAM_ADMIN_ID:
            try:
                room_member = await context.bot.get_chat_member(chat.id, member.id)
                if room_member.status not in ("creator", "administrator"):
                    await context.bot.ban_chat_member(chat.id, member.id)
                    await context.bot.unban_chat_member(chat.id, member.id, only_if_banned=True)
            except Exception:
                pass
    await maybe_publish_middleman_room(context, chat.id)


# ─── Central callback router — NO pre-answer (each handler calls qans) ───────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    user = q.from_user
    parts = data.split(":")

    try:
        prefix = parts[0]
        if prefix == "esc":
            await handle_escrow_cb(q, context, parts, user)
        elif prefix == "deal":
            await handle_deal_action(q, context, parts, user)
        elif prefix == "mm":
            await handle_mm_cb(q, context, parts, user)
        elif prefix == "room":
            await handle_room_cb(q, context, parts, user)
        elif prefix == "wal":
            if len(parts) > 1 and parts[1] == "withdraw_confirm":
                await handle_withdraw_confirm_cb(q, context, user)
            else:
                await handle_wallet_cb(q, context, parts, user)
        elif prefix == "rate":
            await handle_rating_cb(q, context, parts, user)
        elif prefix == "vouch":
            await handle_vouch_cb(q, context, parts, user)
        elif prefix == "admin_vouch":
            if user.id != TELEGRAM_ADMIN_ID:
                await qans(q, "⛔ Admin only.", alert=True)
                return
            await handle_admin_vouch_cb(q, context, parts, user)
        else:
            await qans(q)
    except Exception as e:
        log.error(f"Callback error [{data}]: {e}", exc_info=True)
        await qans(q, "⚠️ An error occurred. Please try again.", alert=True)


# ─── Scheduled jobs ───────────────────────────────────────────────────────────

async def expire_deals(context: ContextTypes.DEFAULT_TYPE = None):
    """Expire deals that have passed their 2h deadline."""
    try:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM deals WHERE status IN ('awaiting_payment','in_progress','buyer_paid') "
                "AND expires_at IS NOT NULL AND expires_at < $1",
                now_iso(),
            )
        for r in rows:
            deal = dict(r)
            deal_id = deal["deal_id"]
            await db.update_deal(deal_id, status="expired")

            # Refund locked VC to buyer
            if deal.get("locked_amount", 0) > 0:
                await db.adjust_balance(
                    deal["buyer_id"],
                    delta_balance=deal["locked_amount"],
                    delta_locked=-deal["locked_amount"],
                )

            # Edit group card
            deal["status"] = "expired"
            card_text, _ = build_deal_card(deal)
            try:
                if deal.get("chat_id") and deal.get("message_id"):
                    await _bot_app.bot.edit_message_text(
                        card_text,
                        chat_id=deal["chat_id"],
                        message_id=deal["message_id"],
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=None,
                    )
            except Exception as e:
                log.warning(f"expire edit: {e}")

            await send_dm(deal["buyer_id"],
                          f"⏰ *Deal \\#{esc(deal_id)} expired* \\(2h timeout\\)\\. "
                          f"Any locked VC has been refunded\\.")
            await send_dm(deal["seller_id"],
                          f"⏰ *Deal \\#{esc(deal_id)} expired* \\(2h timeout\\)\\.")
            log.info(f"Deal {deal_id} expired")
    except Exception as e:
        log.error(f"expire_deals: {e}", exc_info=True)


async def purge_expired_media_job(context: ContextTypes.DEFAULT_TYPE = None):
    """Auto-delete media mirrored to private storage channel after 24 hours."""
    if not _bot_app:
        return
    try:
        now_str = datetime.now(timezone.utc).isoformat()
        due_list = await db.get_due_media_purges(now_str)
        for item in due_list:
            p_id = item["id"]
            chat_id = item["chat_id"]
            msg_id = item["message_id"]
            ref_id = item.get("ref_id") or ""
            try:
                await _bot_app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                log.info(f"Purged 24h media message {msg_id} ({ref_id}) from channel {chat_id}")
            except Exception as ex:
                err_str = str(ex).lower()
                if "message to delete not found" in err_str or "message can't be deleted" in err_str:
                    log.info(f"Purge media {msg_id} already deleted: {ex}")
                else:
                    log.warning(f"Could not purge storage message {msg_id}: {ex}")
            await db.remove_media_purge(p_id)
    except Exception as e:
        log.error(f"purge_expired_media_job error: {e}", exc_info=True)


# ─── Broadcast (called from admin API) ───────────────────────────────────────

async def broadcast_message(text: str) -> int:
    """Send a message to all users. Returns count of successful sends."""
    if not _bot_app:
        return 0
    users = await db.get_all_users(limit=10000)
    count = 0
    for u in users:
        if u.get("notify_enabled", True):
            ok = await send_dm(u["tg_id"], esc(text), parse_mode=None)
            if ok:
                count += 1
            await asyncio.sleep(0.05)  # Telegram rate limit
    return count


# ─── Bot setup & lifecycle ────────────────────────────────────────────────────

async def _reconcile_state():
    """Rebuild in-memory routing state (RELAY, EVIDENCE_AWAIT) from the DB on startup.

    RELAY/EVIDENCE_AWAIT/etc. live only in process memory — every restart (deploy, crash, or a
    dev `--reload`) wipes them for anyone with a deal/dispute already in flight. Without this,
    the next message from a party mid-deal or mid-dispute (e.g. a dispute evidence screenshot)
    would silently misroute until they re-triggered the relevant action from scratch."""
    relay_count = 0
    evidence_count = 0
    try:
        for status in ("in_progress", "buyer_paid"):
            for deal in await db.get_deals(status=status, limit=500):
                if deal.get("buyer_id"):
                    RELAY[deal["buyer_id"]] = {
                        "deal_id": deal["deal_id"], "other_id": deal.get("seller_id"),
                        "other_un": deal.get("seller_un"), "role": "buyer",
                    }
                    relay_count += 1
                if deal.get("seller_id"):
                    RELAY[deal["seller_id"]] = {
                        "deal_id": deal["deal_id"], "other_id": deal.get("buyer_id"),
                        "other_un": deal.get("buyer_un"), "role": "seller",
                    }
                    relay_count += 1

        for deal in await db.get_deals(status="disputed", limit=500):
            dispute = await db.get_dispute(deal["deal_id"])
            if not dispute or dispute.get("status") != "open":
                continue
            targets = [deal.get("buyer_id"), deal.get("seller_id")]
            for uid in targets:
                if uid:
                    EVIDENCE_AWAIT[uid] = {"deal_id": deal["deal_id"], "count": 0}
                    evidence_count += 1
    except Exception as e:
        log.error(f"reconcile_state failed: {e}", exc_info=True)

    if relay_count or evidence_count:
        log.info(f"reconcile_state: restored {relay_count} RELAY / {evidence_count} EVIDENCE_AWAIT entries after restart")

    try:
        current_ch = await db.get_media_storage_channel()
        if not current_ch:
            await db.set_media_storage_channel(-1004495812759)
            log.info("Initialized default media storage channel: -1004495812759")
    except Exception as ex:
        log.warning(f"Could not auto-set default storage channel: {ex}")


async def start_bot():
    global _bot_app
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    _bot_app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )
    app = _bot_app

    if auto_ltc.enabled():
        from auto_ltc_bot import register
        await register(app)

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("escrow", cmd_escrow))
    app.add_handler(CommandHandler("registerroom", cmd_register_room))
    app.add_handler(CommandHandler("rooms", cmd_room_status))
    app.add_handler(CommandHandler("roomstatus", cmd_room_status))
    app.add_handler(CommandHandler("resetroom", cmd_reset_room))
    app.add_handler(CommandHandler("registerstorage", cmd_register_storage))
    app.add_handler(CommandHandler("setstorage", cmd_set_storage))
    app.add_handler(CommandHandler("storagestatus", cmd_storage_status))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("rep", cmd_rep))
    app.add_handler(CommandHandler("mydeals", cmd_mydeals))
    app.add_handler(CommandHandler("transcript", cmd_transcript))

    # +vouch (text message starting with +vouch)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^\+vouch"), handle_vouch
    ))

    # Group: new member
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))

    # Messages in an assigned private room are saved for the final DM transcript.
    # Priority -1 lets the existing group handlers continue for ordinary groups.
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.VOICE | filters.AUDIO | filters.ANIMATION)
        & ~filters.COMMAND & ~filters.ChatType.PRIVATE,
        handle_room_message,
    ), group=-1)

    # Photos in DM — relay / vouch proof / dispute evidence / wallet deposit.
    # Also catch screenshots sent as an uncompressed image "file"/document — common on desktop —
    # which previously arrived as a `document` update and were silently dropped entirely.
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.Document.IMAGE) & filters.ChatType.PRIVATE, handle_dm_photo
    ))

    # All text (non-command)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # Set bot commands
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "Start the bot"),
            BotCommand("wallet", "Your VC wallet (DM only)"),
            BotCommand("mydeals", "View your active deals"),
        ], scope=BotCommandScopeAllPrivateChats())
        await app.bot.set_my_commands([
            BotCommand("escrow", "Start a new escrow deal"),
            BotCommand("rep", "View user trust profile"),
        ], scope=BotCommandScopeAllGroupChats())
    except Exception as e:
        log.warning(f"set_my_commands: {e}")

    # Scheduler
    # Pass coroutine functions directly so APScheduler schedules them on its active event loop.
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        expire_deals,
        "interval", minutes=1, id="expire_deals",
    )
    scheduler.add_job(
        nudge_stale_deals,
        "interval", minutes=30, id="nudge_deals", replace_existing=True,
    )
    scheduler.add_job(
        purge_expired_media_job,
        "interval", minutes=10, id="purge_media", replace_existing=True,
    )
    scheduler.start()

    await _reconcile_state()

    # Start polling
    await app.initialize()
    await app.start()

    # Save bot username for deep links (after initialize)
    global _bot_username
    try:
        me = await app.bot.get_me()
        _bot_username = me.username or ""
        log.info(f"Bot username: @{_bot_username}")
    except Exception as e:
        log.warning(f"get_me: {e}")

    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram bot polling started")


async def stop_bot():
    global _bot_app
    if _bot_app:
        try:
            await _bot_app.updater.stop()
            await _bot_app.stop()
            await _bot_app.shutdown()
        except Exception as e:
            log.warning(f"stop_bot: {e}")
        _bot_app = None
    log.info("Telegram bot stopped")
