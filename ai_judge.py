"""Agentic AI dispute judge (+ deposit verifier) for Vault-Pay escrow, powered by OpenRouter.

Scope: the dispute judge only runs on wallet-based deals (payment_method == 'wallet'). Middleman
deals move funds through a third-party human off-platform — there's no ledger the AI can actually
verify, and the middleman could be the one scamming. Those disputes stay human-only.

Dispute pipeline:
1. Both parties submit evidence (text/screenshots) via bot.py's EVIDENCE_AWAIT flow.
2. `review_dispute()` builds a full case dossier (deal + evidence + cross-deal history +
   precedents) and does a cheap single-call TRIAGE pass first — it has the full picture of both
   sides' story before deciding anything. If it's confident more evidence is needed, that's a
   single AI call (cheap, and reduces daily OpenRouter-quota pressure that was causing the old
   "keeps asking / screenshots not analyzed" failures). Only once triage thinks the case is
   decidable does the heavier 3-persona jury + chief-justice pass run for the actual binding
   verdict. Jury disagreement or low confidence always escalates to a human, regardless of amount.
3. `apply_verdict()` acts on it:
   - need_more_evidence: DMs *targeted, case-specific* follow-up questions only to the party
     missing something, remembers what was already asked (so it stops repeating itself), and
     sets a 24h evidence deadline for the autonomous evidence-chase job in bot.py.
   - escalated: both parties get a short heads-up DM, admin gets the full reasoning.
   - a resolution verdict, small + confident + not escalated: auto-executes via
     admin_api.execute_dispute_resolution (which itself DMs both parties + admin the full report
     and updates the group card — single source of truth for every resolution path).
4. `file_appeal()` lets either party ask for one re-review within 24h of resolution — it never
   auto-reverses funds, only flags the case for urgent human re-check with a fresh AI opinion.

Deposit verification: `verify_deposit()` looks at a deposit screenshot with vision + the claimed
amount and either auto-credits the wallet, auto-rejects on clear mismatch/fraud signs, or leaves
it pending for manual admin review when the model isn't confident either way.
"""
import base64
import hashlib
import json
import logging
import sys
import os
from datetime import datetime, timedelta

_LIBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_libs")
if os.path.isdir(_LIBS_DIR) and _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

import httpx

import db
from config import (
    OPENROUTER_API_KEY, AI_JUDGE_MODEL, AI_AUTO_THRESHOLD, AI_AUTO_CONFIDENCE_MIN,
    TELEGRAM_ADMIN_ID, ADMIN_UPI_ID,
)

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
VALID_VERDICTS = ("release_seller", "refund_buyer", "split", "need_more_evidence")
EVIDENCE_DEADLINE_HOURS = 24
APPEAL_WINDOW_HOURS = 24
DEPOSIT_AUTO_APPROVE_CONFIDENCE = 70
DEPOSIT_AUTO_REJECT_CONFIDENCE = 75

# ─── Live-tunable AI settings (admin panel "AI" tab writes these; DB is the source of truth,
# config.py constants above are only the first-run defaults) ──────────────────────────────────

async def get_ai_settings() -> dict:
    """Every knob the admin AI tab can tune, read fresh from the settings table each call (cheap —
    tiny table) so an admin edit takes effect on the very next dispute/deposit, no restart needed."""
    return {
        "model": await db.get_setting("ai_judge_model", AI_JUDGE_MODEL),
        "auto_threshold": int(await db.get_setting("ai_auto_threshold", AI_AUTO_THRESHOLD)),
        "auto_confidence_min": int(await db.get_setting("ai_auto_confidence_min", AI_AUTO_CONFIDENCE_MIN)),
        "deposit_auto_approve_confidence": int(await db.get_setting(
            "deposit_auto_approve_confidence", DEPOSIT_AUTO_APPROVE_CONFIDENCE)),
        "deposit_auto_reject_confidence": int(await db.get_setting(
            "deposit_auto_reject_confidence", DEPOSIT_AUTO_REJECT_CONFIDENCE)),
        "custom_instructions": await db.get_setting("ai_custom_instructions", ""),
    }


# Full platform briefing given to the AI on every dispute/deposit call, so it reasons with the
# whole system in view rather than just the one deal in front of it — same context a new human
# reviewer would need before they could judge a case fairly.
BOT_CONTEXT = """You are the Senior Compliance and Forensic Dispute Arbitrator for Vault-Pay Escrow.
Your communications are strictly in professional, objective, authoritative English.
You NEVER state or imply that you are an AI, bot, algorithm, or automated system.
You communicate as a seasoned human Senior Dispute Officer / Head of Escrow Compliance.

Core Platform and Legal Principles:
- 100% INNOCENT PARTY SETTLEMENT: Never split settlements (no 30/70 or 50/50). If one party breached terms or attempted fraud, the compliant party receives 100% of their escrow protection (100% Refund to Buyer OR 100% Release to Seller).
- Strict TOC Primacy: If seller agreed to deliver "Original Email (OGE)" or specific inspection window, delivering a disposable temp-mail (e.g. throwaway, guerrillamail, tempmail) is a per-se material breach of contract.
- Inspection Window: The buyer must report defects within the agreed inspection window (standard 15-30 minutes).
- Proof Burden: Claims must be substantiated by timestamps, verifiable screenshots, or transaction logs.
- Evidence Integrity: Evaluate all chat transcripts, screenshot error codes, user trade histories, and counterparty claims with institutional forensic rigor."""


def _wrap_prompt(base_prompt: str, custom_instructions: str = "") -> str:
    """Prefixes the full bot context (and any admin-provided training notes) onto a role prompt."""
    parts = [BOT_CONTEXT, base_prompt]
    if custom_instructions:
        parts.append(f"── Admin's additional training notes for you \\(follow these\\) ──\n{custom_instructions}")
    return "\n\n".join(parts)

VERDICT_SCHEMA_INSTRUCTIONS = """Respond with STRICT JSON ONLY (no markdown fences, no prose outside the JSON object), exactly this shape:
{
  "verdict": "release_seller" | "refund_buyer" | "split" | "need_more_evidence",
  "confidence": <integer 0-100>,
  "reasoning": "<2-4 sentences citing specific evidence you saw>",
  "split_ratio": {"seller": <0-100>, "buyer": <0-100>},
  "follow_up_questions": {"buyer": "<question or empty string>", "seller": "<question or empty string>"}
}
Only fill "split_ratio" when verdict is "split". Only fill "follow_up_questions" when verdict is \
"need_more_evidence" — ask exactly what's missing for THIS specific case (reference the actual claims made), \
never a generic template question. If a "Questions already asked" list is given below, do not repeat them — \
either ask something new and more specific, or if nothing new can reasonably be asked, stop asking and decide \
based on the evidence available (treat continued silence on a repeated ask as evidence against that party). \
The party who OPENED the dispute is making the accusation — interrogate them first and most thoroughly (their \
claim needs to hold up before anyone else's story matters); only raise a follow-up for the OTHER party when \
their specific defence actually needs testing, and phrase it differently from anything already asked of them."""

TRIAGE_PROMPT = """You are Vault-Pay's triage reviewer. Read the FULL deal details and both parties' \
sides of the story below before deciding anything. The party who OPENED the dispute is making an \
accusation — check their claim first and thoroughly; it needs to actually hold up before the other \
party's defence is even worth weighing. Your only job right now is: is there enough here to \
reach a fair verdict, or is something specific and important still missing? Be economical — most cases \
that already have both parties' explanations plus at least one piece of concrete evidence (screenshot, \
transaction reference, timestamp) are decidable; don't ask just to be thorough. Only pick \
"need_more_evidence" if a claim central to the outcome is genuinely unverifiable as-is, and your question \
must reference the specific claim, not a generic template. If you believe the case IS decidable, give your \
own verdict/confidence/reasoning anyway (it may be used directly or double-checked by a second pass)."""

JURY_PERSONAS = {
    "strict_evidentiary": (
        "You are a strict evidentiary judge. You only trust verifiable proof (transaction IDs, "
        "timestamped screenshots, matching amounts). Unverifiable claims count for nothing. "
        "When both sides lack proof, you lean toward the safer outcome (refund/split) rather than "
        "rewarding an unproven claim."
    ),
    "contextual": (
        "You are a contextual, common-sense judge. You weigh plausibility, tone, response speed, "
        "and consistency of each party's story, not just hard proof — real disputes rarely come with "
        "perfect evidence. You still won't rule for a story that doesn't add up."
    ),
    "fraud_skeptic": (
        "You are a fraud-skeptic judge. You actively look for scam patterns: doctored screenshots, "
        "mismatched amounts/names, stalling tactics, a party going silent right after being asked a "
        "pointed question, or an evidence story that changes between messages. You are the most "
        "suspicious of the three judges."
    ),
}

CHIEF_JUSTICE_PROMPT = """You are the Chief Justice of Vault-Pay's AI dispute panel. Three associate \
judges each independently reviewed the same case and gave a verdict. Your job is to weigh their \
opinions, resolve disagreement, and issue ONE final verdict. If the judges strongly disagree or all \
have low confidence, reflect that with a lower final confidence rather than forcing false certainty — \
downstream logic will escalate low-confidence/disagreeing cases to a human anyway, so be honest."""


def esc_md(text) -> str:
    if text is None:
        return ""
    chars = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in chars else c for c in str(text))


def _iso_plus_hours(hours: int) -> str:
    return (datetime.utcnow() + timedelta(hours=hours)).isoformat()


async def _fetch_photo_bytes(file_id: str) -> bytes | None:
    """Download a Telegram photo by file_id and return the raw bytes (for vision input and/or hashing)."""
    try:
        import bot as bot_module
        if not bot_module._bot_app:
            log.warning("ai_judge: _bot_app not ready, cannot fetch photo for AI analysis")
            return None
        tg_file = await bot_module._bot_app.bot.get_file(file_id)
        raw = await tg_file.download_as_bytearray()
        if not raw:
            log.warning(f"ai_judge: downloaded 0 bytes for file_id {file_id}")
            return None
        return bytes(raw)
    except Exception as e:
        log.error(f"ai_judge: photo fetch failed for {file_id}: {e}", exc_info=True)
        return None


async def _fetch_photo_data_url(file_id: str) -> str | None:
    """Download a Telegram photo by file_id and return a base64 data: URL for vision input."""
    raw = await _fetch_photo_bytes(file_id)
    if not raw:
        return None
    b64 = base64.b64encode(raw).decode()
    return f"data:image/jpeg;base64,{b64}"


async def _build_case_content(deal: dict, dispute: dict, note: str = "") -> list:
    """Builds the multimodal `content` list (text + any evidence images) shared by every judge call."""
    evidence = dispute.get("messages", [])
    buyer_hist = await db.get_user_dispute_history(deal.get("buyer_id"), exclude_deal_id=deal.get("deal_id"))
    seller_hist = await db.get_user_dispute_history(deal.get("seller_id"), exclude_deal_id=deal.get("deal_id"))
    precedents = await db.get_resolved_precedents(limit=5)
    asked = json.loads(dispute.get("ai_asked_questions") or "[]")

    payment_method = deal.get("payment_method") or "wallet"
    pm_note = (
        "wallet — funds are on-platform, a decisive verdict here can execute directly"
        if payment_method == "wallet" else
        "middleman — real funds are held off-platform by a human; your verdict is a RECOMMENDATION "
        "only, it will be handed to the middleman/admin to actually act on, never auto-executed"
    )
    opener_role = "buyer" if dispute.get("opened_by") == deal.get("buyer_id") else "seller"

    lines = [
        f"Deal ID: {deal.get('deal_id')}",
        f"Product/service: {deal.get('product_name') or 'not specified'}",
        f"Amount: {deal.get('amount')} VC | Fee payer: {deal.get('fee_payer')} | Escrow mode: {pm_note}",
        f"Dispute opened by: {opener_role}",
        f"Buyer: @{deal.get('buyer_un') or deal.get('buyer_id')} — prior disputes: {buyer_hist['total']} "
        f"(resolved against them: {buyer_hist['resolved_against']}, in their favor: {buyer_hist['resolved_favor']})",
        f"Seller: @{deal.get('seller_un') or deal.get('seller_id')} — prior disputes: {seller_hist['total']} "
        f"(resolved against them: {seller_hist['resolved_against']}, in their favor: {seller_hist['resolved_favor']})",
        f"Deal terms/ToC: {(deal.get('toc') or 'none specified')[:800]}",
        f"Deal created: {deal.get('created_at', 'unknown')} | Paid at: {deal.get('paid_at') or 'not marked paid'}",
        "",
        "── Evidence submitted, i.e. both parties' full side of the story (chronological) ──",
    ]
    if not evidence:
        lines.append("(No evidence was submitted by either party yet.)")
    for m in evidence:
        who = m.get("from", "unknown")
        if m.get("type") == "photo":
            lines.append(f"[{m.get('at', '')}] @{who} submitted a SCREENSHOT (attached as an image below — look at it).")
        else:
            lines.append(f"[{m.get('at', '')}] @{who}: {m.get('text', '')}")

    if asked:
        lines.append("")
        lines.append("── Questions already asked in previous rounds (do NOT repeat these) ──")
        for q in asked[-6:]:
            lines.append(f"- {q}")

    if precedents:
        lines.append("")
        lines.append("── Recent precedent verdicts on other deals (for consistency, not binding) ──")
        for p in precedents:
            lines.append(f"- {p['resolution']}: {p['reasoning']}")

    if note:
        lines.append("")
        lines.append(f"── Automated note ──\n{note}")

    content = [{"type": "text", "text": "\n".join(lines)}]
    photo_count = 0
    for m in evidence:
        if m.get("type") == "photo" and photo_count < 6:
            data_url = await _fetch_photo_data_url(m["file_id"])
            if data_url:
                content.append({"type": "image_url", "image_url": {"url": data_url}})
                photo_count += 1
            else:
                lines_note = f"[NOTE: the screenshot from @{m.get('from')} at {m.get('at','')} could not be downloaded for AI analysis — do not assume it supports either side.]"
                content[0]["text"] += "\n" + lines_note
    return content


def _parse_verdict_json(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
    except Exception:
        return None
    if parsed.get("verdict") not in VALID_VERDICTS:
        return None
    return parsed


async def _call_openrouter_raw(system_prompt: str, content, model: str = None) -> dict | None:
    if not OPENROUTER_API_KEY:
        return None
    if not model:
        settings = await get_ai_settings()
        model = settings["model"]
    payload = {
        "model": model or AI_JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json=payload,
            )
        if resp.status_code == 429:
            log.error(f"ai_judge: OpenRouter rate-limited (429): {resp.text[:300]}")
            return None
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.error(f"ai_judge: OpenRouter call failed: {e}")
        return None


async def _call_openrouter(system_prompt: str, content, model: str = None) -> dict | None:
    raw = await _call_openrouter_raw(system_prompt, content, model=model)
    if raw is None:
        return None
    return _parse_verdict_json(raw)


async def _run_full_jury(deal_id: str, content: list, custom_instructions: str = "", model: str = None) -> dict:
    """The expensive, high-stakes pass: 3 personas in parallel + a chief-justice consolidation."""
    import asyncio

    base_schema = "\n\n" + VERDICT_SCHEMA_INSTRUCTIONS
    jury_calls = {
        name: _call_openrouter(_wrap_prompt(persona, custom_instructions) + base_schema, content, model=model)
        for name, persona in JURY_PERSONAS.items()
    }
    results = await asyncio.gather(*jury_calls.values())
    jury = {name: v for name, v in zip(jury_calls.keys(), results) if v}

    if not jury:
        return {"error": "AI jury calls all failed"}

    jury_summary_lines = [
        f"- {name}: verdict={v.get('verdict')} confidence={v.get('confidence')} reasoning={v.get('reasoning', '')[:200]}"
        for name, v in jury.items()
    ]
    chief_content = [
        {"type": "text", "text": (
            content[0]["text"] + "\n\n── Associate judge opinions ──\n" + "\n".join(jury_summary_lines)
        )}
    ] + content[1:]

    final = await _call_openrouter(
        _wrap_prompt(CHIEF_JUSTICE_PROMPT, custom_instructions) + "\n\n" + VERDICT_SCHEMA_INSTRUCTIONS,
        chief_content, model=model,
    )
    if not final:
        final = next(iter(jury.values()))
        final["reasoning"] = "(Chief justice pass unavailable; using associate judge verdict.) " + final.get("reasoning", "")

    verdicts_seen = {v.get("verdict") for v in jury.values()}
    unanimous = len(verdicts_seen) <= 1
    low_confidence = int(final.get("confidence", 0) or 0) < 50
    final["jury_unanimous"] = unanimous
    final["escalate"] = (not unanimous) or low_confidence
    final["model"] = model or AI_JUDGE_MODEL

    await db.update_dispute(
        deal_id,
        ai_verdict_json=json.dumps(final),
        ai_checked_at=db.now_iso(),
        jury_json=json.dumps(jury),
        escalated=final["escalate"],
    )
    return final


async def review_dispute(deal_id: str, deadline_note: str = "", force_final: bool = False) -> dict:
    """Full review pipeline: cheap triage first, full jury only once the case looks decidable
    (or force_final=True, used by the evidence-deadline chase and appeals). Runs for BOTH wallet
    and middleman deals — apply_verdict() is what decides whether a resolution verdict actually
    moves funds (wallet) or is only handed to the middleman/admin as a recommendation (MM)."""
    if not OPENROUTER_API_KEY:
        return {"error": "OPENROUTER_API_KEY not configured"}

    deal = await db.get_deal(deal_id)
    dispute = await db.get_dispute(deal_id)
    if not deal or not dispute:
        return {"error": "deal or dispute not found"}

    settings = await get_ai_settings()
    custom = settings["custom_instructions"]
    model = settings["model"]

    content = await _build_case_content(deal, dispute, note=deadline_note)

    if not force_final:
        triage = await _call_openrouter(
            _wrap_prompt(TRIAGE_PROMPT, custom) + "\n\n" + VERDICT_SCHEMA_INSTRUCTIONS, content, model=model,
        )
        if triage and triage.get("verdict") == "need_more_evidence":
            triage["jury_unanimous"] = True
            triage["escalate"] = False
            triage["model"] = model
            await db.update_dispute(
                deal_id,
                ai_verdict_json=json.dumps(triage),
                ai_checked_at=db.now_iso(),
            )
            return triage
        # Triage thinks it's decidable (or triage itself failed) — fall through to the full jury
        # for a properly considered, harder-to-fool final verdict.

    return await _run_full_jury(deal_id, content, custom_instructions=custom, model=model)


# Kept as an alias: earlier code / callers refer to this name.
judge_dispute = review_dispute


async def _send_analysing_notices(deal: dict) -> list:
    """Tell both parties the AI is working, so the wait doesn't feel broken. Cleared once done."""
    from bot import _bot_app
    sent = []
    if not _bot_app:
        return sent
    for uid in [deal.get("buyer_id"), deal.get("seller_id")]:
        if not uid:
            continue
        try:
            m = await _bot_app.bot.send_message(uid, "🤖 Analysing your query...")
            sent.append((uid, m.message_id))
        except Exception:
            pass
    return sent


async def _clear_analysing_notices(sent: list):
    from bot import _bot_app
    if not _bot_app:
        return
    for uid, mid in sent:
        try:
            await _bot_app.bot.delete_message(uid, mid)
        except Exception:
            pass


async def _send_mm_recommendation_report(deal: dict, action: str, confidence: int, reasoning: str):
    """Middleman deals have no on-platform ledger the AI can move — so a resolution verdict is
    delivered as a full recommendation (with both usernames) to whoever CAN act on it: the
    middleman actually assigned to this deal (if one entered it) and the admin, plus a shorter
    heads-up analysis to both buyer and seller so no one is left in the dark."""
    from bot import send_dm, esc as bot_esc, MM_STATE

    label_map = {
        "release_seller": "release the held payment to the SELLER",
        "refund_buyer": "refund the held payment to the BUYER",
        "split": "split the held payment 50/50 between buyer and seller",
    }
    action_label = label_map.get(action, action)
    buyer_un = deal.get("buyer_un") or str(deal.get("buyer_id"))
    seller_un = deal.get("seller_un") or str(deal.get("seller_id"))

    mm_st = MM_STATE.get(deal["deal_id"], {})
    mm_id = mm_st.get("mm_id")

    detail_msg = (
        f"🤖 *AI Judge recommendation \\(Middleman deal \\#{bot_esc(deal['deal_id'])}\\)*\n"
        f"Suggested action: *{bot_esc(action_label)}*\n"
        f"Confidence: {confidence}%\n"
        f"Reasoning: {bot_esc(reasoning[:500])}\n\n"
        f"Buyer: @{bot_esc(buyer_un)} \\| Seller: @{bot_esc(seller_un)}\n"
        f"This is a MIDDLEMAN deal \\— the AI cannot move these funds itself\\. Please coordinate "
        f"with both parties directly \\(DM or via the bot\\) to act on this\\."
    )
    recipients = [TELEGRAM_ADMIN_ID]
    if mm_id and mm_id not in recipients:
        recipients.append(mm_id)
    for uid in recipients:
        if uid:
            await send_dm(uid, detail_msg)

    summary_msg = (
        f"⚖️ *AI Judge Analysis — Deal \\#{bot_esc(deal['deal_id'])} \\(Middleman\\)*\n"
        f"Recommendation: *{bot_esc(action_label)}*\n"
        f"Reasoning: {bot_esc(reasoning[:500])}\n\n"
        f"This deal used a middleman, so the AI cannot transfer funds directly \\— your middleman/admin "
        f"has this recommendation and both your usernames to act on it\\."
    )
    for uid in [deal.get("buyer_id"), deal.get("seller_id")]:
        if uid:
            await send_dm(uid, summary_msg)


async def apply_verdict(deal_id: str, verdict: dict) -> dict:
    """Act on a verdict: auto-resolve, ask for more evidence, or flag for admin confirmation."""
    import admin_api  # local import to avoid circular import at module load time
    from bot import send_dm, EVIDENCE_AWAIT

    deal = await db.get_deal(deal_id)
    if not deal:
        return {"outcome": "error", "detail": "deal not found"}

    payment_method = deal.get("payment_method") or "wallet"
    action = verdict.get("verdict")
    confidence = int(verdict.get("confidence", 0) or 0)
    reasoning = verdict.get("reasoning", "")
    escalate = bool(verdict.get("escalate"))

    if action == "need_more_evidence":
        qs = verdict.get("follow_up_questions", {}) or {}
        dispute = await db.get_dispute(deal_id)
        asked = json.loads(dispute.get("ai_asked_questions") or "[]") if dispute else []
        pending = []

        if deal.get("buyer_id") and qs.get("buyer"):
            EVIDENCE_AWAIT[deal["buyer_id"]] = {"deal_id": deal_id, "count": 0}
            pending.append(deal["buyer_id"])
            asked.append(f"[buyer] {qs['buyer']}")
            await send_dm(
                deal["buyer_id"],
                f"⚖️ *Senior Dispute Resolution Desk \\(Case \\#{esc_md(deal_id)}\\):*\n"
                f"{esc_md(qs['buyer'])}\n\n"
                f"Please reply directly to this message with specific evidence and screenshots, then type *done*\\.\n"
                f"Response window: *{EVIDENCE_DEADLINE_HOURS} hours* to enable final case determination\\.",
            )
        if deal.get("seller_id") and qs.get("seller"):
            EVIDENCE_AWAIT[deal["seller_id"]] = {"deal_id": deal_id, "count": 0}
            pending.append(deal["seller_id"])
            asked.append(f"[seller] {qs['seller']}")
            await send_dm(
                deal["seller_id"],
                f"⚖️ *Senior Dispute Resolution Desk \\(Case \\#{esc_md(deal_id)}\\):*\n"
                f"{esc_md(qs['seller'])}\n\n"
                f"Please reply directly to this message with specific evidence and screenshots, then type *done*\\.\n"
                f"Response window: *{EVIDENCE_DEADLINE_HOURS} hours* to enable final case determination\\.",
            )

        await db.update_dispute(
            deal_id,
            ai_pending_from=json.dumps(pending),
            ai_asked_questions=json.dumps(asked[-12:]),
            evidence_deadline=_iso_plus_hours(EVIDENCE_DEADLINE_HOURS) if pending else "",
        )
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"⚖️ *Senior Dispute Desk* requested clarification for Deal \\#{esc_md(deal_id)} \\(window {EVIDENCE_DEADLINE_HOURS}h\\)\\.",
        )
        return {"outcome": "more_evidence"}

    if action not in ("release_seller", "refund_buyer"):
        return {"outcome": "error", "detail": f"unexpected verdict '{action}'"}

    if payment_method != "wallet":
        # No on-platform ledger to move — the verdict is a full recommendation to the middleman
        # (and admin), never an auto-execution. Still recorded via review_dispute() above.
        await _send_mm_recommendation_report(deal, action, confidence, reasoning)
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"🤖 *AI Judge* reviewed middleman Deal \\#{esc_md(deal_id)} \\— recommendation: "
            f"*{esc_md(action)}* \\({confidence}%\\)\\. Full details sent above\\.",
        )
        return {"outcome": "mm_recommended", "action": action}

    settings = await get_ai_settings()
    amount = deal.get("amount", 0) or 0
    auto_ok = (not escalate) and amount <= settings["auto_threshold"] and confidence >= settings["auto_confidence_min"]

    if auto_ok:
        await admin_api.execute_dispute_resolution(deal_id, action, resolved_by="ai_judge")
        await db.update_dispute(deal_id, ai_resolved_auto=True)
        return {"outcome": "auto_resolved", "action": action}
    else:
        if escalate:
            for uid in [deal.get("buyer_id"), deal.get("seller_id")]:
                if uid:
                    await send_dm(
                        uid,
                        f"⚠️ *Deal \\#{esc_md(deal_id)}:* your dispute needs a closer human look "
                        f"before a decision is made\\. Admin has been notified — hang tight\\.",
                    )
            prefix = "🚨 *ESCALATED* \\— jury disagreed or low confidence\\. "
        else:
            prefix = ""
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"{prefix}🤖 *AI Judge recommendation for Deal \\#{esc_md(deal_id)}*\n"
            f"Suggested: *{esc_md(action)}* \\(confidence {confidence}%\\)\n"
            f"Reason: {esc_md(reasoning[:400])}\n\nOpen the admin panel to confirm or override\\.",
        )
        return {"outcome": "needs_admin", "action": action, "escalated": escalate}


async def judge_and_apply(deal_id: str, deadline_note: str = "", force_final: bool = False) -> dict:
    """Convenience wrapper: shows an 'Analysing...' notice, runs review_dispute, applies the verdict.
    Runs for both wallet and middleman deals now — apply_verdict() branches on payment_method to
    either execute funds (wallet) or send a recommendation to the middleman/admin (MM)."""
    deal = await db.get_deal(deal_id)

    notices = await _send_analysing_notices(deal) if deal else []
    try:
        verdict = await review_dispute(deal_id, deadline_note=deadline_note, force_final=force_final)
    finally:
        await _clear_analysing_notices(notices)

    if verdict.get("error"):
        return {"outcome": "error", "detail": verdict["error"]}
    result = await apply_verdict(deal_id, verdict)
    result["verdict"] = verdict
    return result


async def file_appeal(deal_id: str, requester_id: int) -> dict:
    """One re-review per dispute. Never auto-reverses funds — flags for urgent human check."""
    from bot import send_dm

    deal = await db.get_deal(deal_id)
    dispute = await db.get_dispute(deal_id)
    if not deal or not dispute:
        return {"ok": False, "detail": "Deal or dispute not found."}
    if dispute.get("status") != "resolved":
        return {"ok": False, "detail": "This dispute isn't resolved yet."}
    if requester_id not in (deal.get("buyer_id"), deal.get("seller_id")):
        return {"ok": False, "detail": "Only the buyer or seller on this deal can appeal it."}
    if dispute.get("appeal_used"):
        return {"ok": False, "detail": "This dispute has already used its one appeal."}

    resolved_at = dispute.get("resolved_at") or ""
    try:
        resolved_dt = datetime.fromisoformat(resolved_at)
        if datetime.utcnow() - resolved_dt > timedelta(hours=APPEAL_WINDOW_HOURS):
            return {"ok": False, "detail": f"Appeal window ({APPEAL_WINDOW_HOURS}h after resolution) has passed."}
    except Exception:
        pass

    await db.update_dispute(deal_id, appeal_used=True, escalated=True)

    note = (
        f"This is an APPEAL of a previously resolved dispute. Original resolution: "
        f"'{dispute.get('resolution')}' by {dispute.get('resolved_by')}. Re-review the full case "
        f"critically — do not simply repeat the prior verdict. This verdict will be shown to a human "
        f"admin only; it will NOT auto-execute any fund movement."
    )
    verdict = await review_dispute(deal_id, deadline_note=note, force_final=True)
    appeal_verdict = {"verdict": "n/a", "confidence": 0}
    if not verdict.get("error"):
        await db.update_dispute(deal_id, appeal_verdict_json=json.dumps(verdict))
        appeal_verdict = verdict

    await send_dm(
        TELEGRAM_ADMIN_ID,
        f"🚨 *APPEAL FILED* on Deal \\#{esc_md(deal_id)} by {'buyer' if requester_id == deal.get('buyer_id') else 'seller'}\\.\n"
        f"Original: *{esc_md(dispute.get('resolution'))}* by {esc_md(dispute.get('resolved_by'))}\\.\n"
        f"AI re\\-review: *{esc_md(appeal_verdict.get('verdict', 'n/a'))}* "
        f"\\(confidence {appeal_verdict.get('confidence', 0)}%\\)\\.\n"
        f"Please open the admin panel to dismiss the appeal or apply a new verdict \\(funds were NOT auto\\-reversed\\)\\.",
    )
    return {"ok": True, "detail": "Appeal filed. Admin will manually review; you'll be notified of any change."}


# ─── Deposit screenshot verification ───────────────────────────────────────────

DEPOSIT_VERIFY_PROMPT = """You are Vault-Pay's payment verification assistant. You will see a screenshot \
that a user submitted as proof of a UPI payment, plus the amount they claim to have paid. Look carefully at \
the screenshot for: a success/completed status, the amount shown, and the destination UPI ID/name. Also \
watch for signs the screenshot itself looks edited/doctored (mismatched fonts, misaligned text, inconsistent \
rounding of amounts). Respond with STRICT JSON ONLY, exactly this shape:
{
  "verdict": "approve" | "reject" | "unclear",
  "confidence": <integer 0-100>,
  "detected_amount": <number or null>,
  "reason": "<one sentence — what you saw and why>"
}
Use "approve" only when the screenshot clearly shows a successful payment for approximately the claimed \
amount to the given UPI ID. Use "reject" only when you're confident something is actually wrong — clearly \
different amount, failed/pending status, wrong recipient, or obvious editing/tampering. If the screenshot is \
blurry, cropped, or you're simply not sure, use "unclear" rather than guessing — a human will review it."""


def _parse_deposit_json(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
    except Exception:
        return None
    if parsed.get("verdict") not in ("approve", "reject", "unclear"):
        return None
    return parsed


async def verify_deposit(tx_id: str) -> dict:
    """Vision-check a deposit screenshot against the claimed amount; auto-approve/reject/leave pending.

    Anti-replay: before spending an OpenRouter call, hash the screenshot bytes and check whether
    this EXACT image was already used on a different deposit claim — the classic exploit here is
    submitting the same real payment screenshot over and over to get credited multiple times. A
    hash match auto-rejects immediately regardless of what the vision model would say, and flags
    the account for admin attention."""
    from bot import send_dm

    tx = await db.get_transaction(tx_id)
    if not tx or tx.get("status") != "pending":
        return {"outcome": "skipped", "detail": "not pending"}

    try:
        data = json.loads(tx.get("data_json") or "{}")
    except Exception:
        data = {}

    amount = tx.get("amount", 0)
    file_id = data.get("photo_file_id")
    user_id = tx.get("user_id")

    if not file_id:
        return {"outcome": "skipped", "detail": "no screenshot"}

    raw_bytes = await _fetch_photo_bytes(file_id)
    if not raw_bytes:
        log.error(f"ai_judge: deposit {tx_id} screenshot could not be downloaded — leaving for manual review")
        data["ai_note"] = "AI could not download the screenshot for analysis."
        await db.update_transaction(tx_id, data_json=json.dumps(data))
        return {"outcome": "pending", "detail": "photo fetch failed"}

    image_hash = hashlib.sha256(raw_bytes).hexdigest()
    data["image_hash"] = image_hash
    prior = await db.find_deposit_by_image_hash(image_hash, exclude_tx_id=tx_id)
    if prior:
        prior_data = {}
        try:
            prior_data = json.loads(prior.get("data_json") or "{}")
        except Exception:
            pass
        data["ai_verdict"] = "reject"
        data["ai_confidence"] = 100
        data["ai_note"] = f"Duplicate screenshot — identical image already used on tx {prior['id'][:12]} ({prior.get('status')})."
        data["rejected_by"] = "ai_judge"
        data["rejected_at"] = db.now_iso()
        data["reject_reason"] = "duplicate_screenshot"
        await db.update_transaction(tx_id, status="rejected", data_json=json.dumps(data))
        try:
            await db.increment_abuse_warning(user_id)
        except Exception:
            pass
        await send_dm(
            user_id,
            f"❌ *Deposit rejected — duplicate screenshot*\n"
            f"Amount: {amount:,} VC\n"
            f"This exact payment screenshot was already used on a previous deposit\\. Reusing proof "
            f"of one payment across multiple deposit claims isn't allowed\\.\n\n"
            f"If this is a mistake, contact admin\\.",
        )
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"🚨 *Duplicate deposit screenshot detected\\!*\n"
            f"@{esc_md(data.get('username'))} \\({user_id}\\) submitted tx `{esc_md(tx_id[:12])}` "
            f"\\({amount:,} VC\\) reusing the SAME screenshot as tx `{esc_md(prior['id'][:12])}` "
            f"\\(status: {esc_md(prior.get('status'))}\\)\\. Auto\\-rejected\\. Possible repeat fraud "
            f"attempt \\— consider reviewing this user's history\\.",
        )
        return {"outcome": "rejected", "confidence": 100, "reason": "duplicate_screenshot"}

    if not OPENROUTER_API_KEY:
        await db.update_transaction(tx_id, data_json=json.dumps(data))
        return {"outcome": "skipped", "detail": "no API key"}

    b64 = base64.b64encode(raw_bytes).decode()
    data_url = f"data:image/jpeg;base64,{b64}"

    content = [
        {"type": "text", "text": (
            f"Claimed deposit amount: ₹{amount} (should be paid in INR to UPI ID {ADMIN_UPI_ID or '(not set)'})."
        )},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    settings = await get_ai_settings()
    raw = await _call_openrouter_raw(DEPOSIT_VERIFY_PROMPT, content, model=settings["model"])
    result = _parse_deposit_json(raw) if raw else None

    if not result:
        data["ai_note"] = "AI verification failed (model error) — needs manual review."
        await db.update_transaction(tx_id, data_json=json.dumps(data))
        return {"outcome": "pending", "detail": "AI call failed"}

    verdict = result.get("verdict")
    confidence = int(result.get("confidence", 0) or 0)
    detected = result.get("detected_amount")
    reason = result.get("reason", "")

    data["ai_verdict"] = verdict
    data["ai_confidence"] = confidence
    data["ai_detected_amount"] = detected
    data["ai_note"] = reason

    if verdict == "approve" and confidence >= settings["deposit_auto_approve_confidence"]:
        from db import adjust_balance
        await adjust_balance(user_id, delta_balance=amount)
        data["approved_by"] = "ai_judge"
        data["approved_at"] = db.now_iso()
        await db.update_transaction(tx_id, status="approved", data_json=json.dumps(data))
        await send_dm(
            user_id,
            f"✅ *Auto\\-deposited by AI\\!*\n"
            f"*{amount:,} VC* has been credited to your wallet\\. /wallet to check\\.\n"
            f"_{esc_md(reason)}_",
        )
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"🤖 AI auto\\-approved deposit `{esc_md(tx_id[:12])}` for @{esc_md(data.get('username'))} "
            f"\\({amount:,} VC, confidence {confidence}%\\)\\.",
        )
        return {"outcome": "approved", "confidence": confidence}

    if verdict == "reject" and confidence >= settings["deposit_auto_reject_confidence"]:
        data["rejected_by"] = "ai_judge"
        data["rejected_at"] = db.now_iso()
        data["reject_reason"] = reason
        await db.update_transaction(tx_id, status="rejected", data_json=json.dumps(data))
        await send_dm(
            user_id,
            f"❌ *Deposit rejected by AI*\n"
            f"Amount: {amount:,} VC\n"
            f"Reason: {esc_md(reason)}\n\n"
            f"If you believe this is a mistake, contact admin \\— they can still manually approve it\\.",
        )
        await send_dm(
            TELEGRAM_ADMIN_ID,
            f"🤖 AI auto\\-rejected deposit `{esc_md(tx_id[:12])}` for @{esc_md(data.get('username'))} "
            f"\\({amount:,} VC, confidence {confidence}%\\)\\. Reason: {esc_md(reason)}\\.\n"
            f"You can still manually approve it from the admin panel if the AI got it wrong\\.",
        )
        return {"outcome": "rejected", "confidence": confidence}

    # Ambiguous / low confidence either way — keep pending, but hand the admin the AI's read.
    await db.update_transaction(tx_id, data_json=json.dumps(data))
    await send_dm(
        TELEGRAM_ADMIN_ID,
        f"🤖 AI reviewed deposit `{esc_md(tx_id[:12])}` for @{esc_md(data.get('username'))} but isn't "
        f"confident enough to decide \\({esc_md(verdict)}, {confidence}%\\)\\. Detected amount: "
        f"{esc_md(detected)}\\. Reason: {esc_md(reason)}\\.\nPlease review manually in the admin panel\\.",
    )
    return {"outcome": "pending", "confidence": confidence}


# ─────────────────────────────────────────────────────────────────────────────
# Agentic add-ons: abuse detection, mid-deal scam-signal watch, proactive
# guidance messages. These are deliberately rule-based/regex-first (instant,
# free, no OpenRouter round-trip on every chat message) with an LLM only used
# for the two one-shot generative helpers at the bottom (deal instructions +
# evidence checklist), which run once per deal/dispute rather than per message.
# ─────────────────────────────────────────────────────────────────────────────
import re

# Common English + Hindi/Hinglish (Latin + Devanagari script) abusive terms. Intentionally
# conservative substring/word matches — false negatives are safer than false positives here,
# since this drives real warnings against real users.
_ABUSE_TERMS = [
    r"\bmc\b", r"\bbc\b", r"\bmadarchod\b", r"\bbehenchod\b", r"\bbhenchod\b", r"\bchutiya\b",
    r"\bchutiye\b", r"\bharami\b", r"\bkutte\b", r"\bkutta\b", r"\bkamine\b", r"\bkamina\b",
    r"\bgandu\b", r"\bgaand\b", r"\brandi\b", r"\bsaala\b", r"\bsala\b", r"\blaude\b", r"\blund\b",
    r"\bchod\b", r"\bbhosdi\b", r"\bbhosadi\b", r"\bteri maa\b", r"\btere maa\b",
    r"\bfuck\s*you\b", r"\bfuck\s*off\b", r"\bmotherfucker\b", r"\bbastard\b", r"\bslut\b",
    r"\bwhore\b", r"\basshole\b", r"\bidiot\b.{0,3}\bscammer\b",
]
_ABUSE_RE = re.compile("|".join(_ABUSE_TERMS), re.IGNORECASE)

# Mid-deal scam signals for the Auto-Hold Guardian — none of these alone are proof of a scam,
# but any hit is worth an admin's eyes before funds move.
_SUSPICIOUS_PATTERNS = [
    (re.compile(r"\b(pay|send|transfer).{0,20}\b(outside|bahar|without escrow|bina escrow|directly)\b", re.IGNORECASE),
     "asked to pay outside the escrow/wallet system"),
    (re.compile(r"\b(paytm|phonepe|gpay|google pay|upi|usdt|btc|crypto|bank transfer|account number)\b.{0,25}\b(number|id|address|send here|isse pay)\b", re.IGNORECASE),
     "shared an alternate payment destination mid-deal"),
    (re.compile(r"\b(cancel|refund).{0,15}\b(escrow|deal|dispute)\b.{0,15}\b(and|phir|then)\b.{0,15}\b(pay|send)\b", re.IGNORECASE),
     "asked to cancel the escrow deal and pay separately"),
    (re.compile(r"\b(jaldi|hurry|urgent|right now|abhi|turant).{0,20}\b(pay|paisa|paise|money|payment)\b", re.IGNORECASE),
     "used urgency/pressure language around payment"),
    (re.compile(r"\b(don'?t tell|mat batana|admin ko mat|keep it secret|hide from admin)\b", re.IGNORECASE),
     "asked the other party to hide something from the admin"),
]


def detect_abusive_language(text: str) -> bool:
    """Fast keyword check for gaali/profanity in a user message. No API call, runs on every message."""
    if not text:
        return False
    return bool(_ABUSE_RE.search(text))


def scan_for_scam_signals(text: str) -> str | None:
    """Fast regex scan for mid-deal scam signals (payment-method switch, pressure tactics, etc).
    Returns a human-readable reason string on the first match, or None if nothing looks suspicious."""
    if not text:
        return None
    for pattern, reason in _SUSPICIOUS_PATTERNS:
        if pattern.search(text):
            return reason
    return None


async def generate_deal_instructions(deal: dict) -> str:
    """Live Group-Chat Sentinel: once a deal goes active, generate short, deal-specific
    instructions for both parties on how to safely complete THIS transaction. Falls back to a
    generic safety checklist if OpenRouter is unavailable/fails, so the group message always sends."""
    product = deal.get("product_name") or "the item/service in this deal"
    amount = deal.get("amount", 0)
    role = deal.get("role", "buy")
    method = deal.get("payment_method", "wallet")

    fallback = (
        "🛡️ *Safety Tips for This Deal*\n"
        "• Never pay or share confirmation outside this bot/chat\\.\n"
        "• Buyer: only mark paid/release after actually receiving the item in working condition\\.\n"
        "• Seller: only deliver after payment is locked/confirmed by the bot\\.\n"
        "• Keep all proof \\(screenshots, chat\\) inside this conversation\\.\n"
        "• If anything feels off, use *Raise Dispute* — don't negotiate outside escrow\\."
    )

    if not OPENROUTER_API_KEY:
        return fallback

    prompt = (
        "You are Vault-Pay's deal safety assistant. Given a specific escrow deal, write a SHORT "
        "(4-6 lines max), specific, practical checklist for the buyer and seller on how to safely "
        "complete THIS particular transaction, tailored to the product/service being sold. Mention "
        "concrete verification steps relevant to that product type (e.g. check account recovery info "
        "for a digital account, check serial number/photos for physical goods, test before confirming "
        "for services). End with a one-line reminder to never pay or communicate outside the bot. "
        "Output plain text only, no markdown headers, ready to send directly to a Telegram group. "
        "Keep it under 700 characters."
    )
    content = [{"type": "text", "text": (
        f"Product/service: {product}\nAmount: {amount} VC\nRole context: {role}\nPayment method: {method}"
    )}]
    raw = await _call_openrouter_raw(prompt, content)
    if not raw:
        return fallback
    text = raw.strip()
    if len(text) > 900:
        text = text[:900] + "…"
    return "🛡️ *AI Safety Briefing for This Deal*\n" + esc_md(text).replace("\\n", "\n")


async def generate_evidence_checklist(deal: dict, opener_role: str = "buyer") -> dict:
    """Guide users to submit the right evidence — ROLE-AWARE: the dispute opener and the other party
    get different, complementary asks (a buyer opener needs to prove what's wrong; a seller opener
    needs to prove they delivered; the other party always gets the complementary proof request).
    Returns {"opener": [...], "other": [...]}, 2-3 short deal-specific items each. Falls back to a
    generic-but-still-role-aware checklist if OpenRouter is unavailable."""
    product = deal.get("product_name") or "the item/service in this deal"
    amount = deal.get("amount", 0)
    opener_role = opener_role if opener_role in ("buyer", "seller") else "buyer"
    other_role = "seller" if opener_role == "buyer" else "buyer"

    buyer_side = [
        "Screenshot/photo showing exactly what's wrong or missing with the product/service.",
        "What was promised (deal description) vs. what you actually received — be specific.",
        "Any proof the seller hasn't delivered, or delivered something different than agreed.",
    ]
    seller_side = [
        "Proof you delivered/performed as agreed (screenshot, confirmation, timestamp).",
        "Any communication with the buyer confirming delivery or acceptance.",
        "Your response to the buyer's specific complaint.",
    ]
    fallback = {
        "opener": buyer_side if opener_role == "buyer" else seller_side,
        "other": seller_side if opener_role == "buyer" else buyer_side,
    }

    if not OPENROUTER_API_KEY:
        return fallback

    prompt = _wrap_prompt(
        "You are Vault-Pay's evidence-intake assistant. A dispute was JUST opened on an escrow deal — "
        f"opened by the {opener_role.upper()}. Given the product/service and amount, generate TWO short "
        "lists of what each side should submit, tailored to THIS product (not generic boilerplate):\n"
        f"- 'opener' (2-3 items): what the {opener_role} (who is raising this dispute) should prove. "
        "If they are the buyer, ask for proof of what's wrong/missing/not delivered. If they are the "
        "seller, ask for proof they actually delivered/performed as agreed.\n"
        f"- 'other' (2-3 items): the complementary evidence the {other_role} (responding to the dispute) "
        "should submit to defend their side.\n"
        "Respond with STRICT JSON ONLY: {\"opener\": [\"...\"], \"other\": [\"...\"]}"
    )
    content = [{"type": "text", "text": f"Product/service: {product}\nAmount: {amount} VC\nDispute opened by: {opener_role}"}]
    raw = await _call_openrouter_raw(prompt, content)
    if not raw:
        return fallback
    try:
        cleaned = raw.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        start, end = cleaned.find("{"), cleaned.rfind("}")
        parsed = json.loads(cleaned[start:end + 1])
        opener_items = parsed.get("opener")
        other_items = parsed.get("other")
        if isinstance(opener_items, list) and opener_items and isinstance(other_items, list) and other_items:
            return {
                "opener": [str(i)[:200] for i in opener_items[:3]],
                "other": [str(i)[:200] for i in other_items[:3]],
            }
    except Exception:
        pass
    return fallback


def assess_identity_risk(user_obj, amount: int, risk_amount_threshold: int = 2000) -> tuple[int, str]:
    """Cheap identity heuristic run at deal creation — no external API, uses signals already on the
    Telegram User object. Telegram user IDs are roughly monotonically increasing over time, so a very
    high ID is a decent (not perfect) proxy for a brand-new account. Returns (risk_score 0-100, notes)."""
    notes = []
    score = 0

    tg_id = getattr(user_obj, "id", 0) or 0
    if tg_id > 7_200_000_000:
        score += 40
        notes.append("very new-looking Telegram account (high account ID)")
    elif tg_id > 6_000_000_000:
        score += 20
        notes.append("relatively new-looking Telegram account")

    if not getattr(user_obj, "username", None):
        score += 15
        notes.append("no public @username set")

    first_name = (getattr(user_obj, "first_name", "") or "").strip()
    if not first_name or first_name.isdigit():
        score += 10
        notes.append("suspicious/placeholder display name")

    if amount >= risk_amount_threshold and score >= 40:
        notes.append(f"high-value deal (₹{amount:,}) from a low-signal account — recommend extra verification")

    return min(score, 100), "; ".join(notes)
