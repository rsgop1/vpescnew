"""PDF rendering for preserved private middleman-room transcripts."""
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

_LIBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_libs")
if os.path.isdir(_LIBS_DIR) and _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


IST = ZoneInfo("Asia/Kolkata")
PAGE_WIDTH, PAGE_HEIGHT = A4
_FONT = "Helvetica"
_BOLD_FONT = "Helvetica-Bold"

# Register TrueType fonts with Unicode & symbol support
for _name, _filename in (
    ("VaultPaySans", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ("VaultPaySansBold", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("VaultPaySans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("VaultPaySansBold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("VaultPaySans", "/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
    ("VaultPaySansBold", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
):
    if os.path.exists(_filename) and _name not in pdfmetrics.getRegisteredFontNames():
        try:
            pdfmetrics.registerFont(TTFont(_name, _filename))
        except Exception:
            pass

if "VaultPaySans" in pdfmetrics.getRegisteredFontNames():
    _FONT = "VaultPaySans"
if "VaultPaySansBold" in pdfmetrics.getRegisteredFontNames():
    _BOLD_FONT = "VaultPaySansBold"


EMOJI_MAP = {
    '🤝': '[Deal/Handshake]',
    '💰': '[Money/Paid]',
    '💸': '[Payment Sent]',
    '💵': '[Cash/Funds]',
    '🪙': '[Coin/Crypto]',
    '✅': '[Verified/OK]',
    '✔': '[Checked]',
    '❌': '[Cancelled/No]',
    '✖': '[Cross]',
    '⚠️': '[Alert/Warning]',
    '⚠': '[Alert]',
    '🔥': '[Urgent/Hot]',
    '🛡️': '[Escrow Protected]',
    '🛡': '[Escrow Protected]',
    '👑': '[VIP/Admin]',
    '📦': '[Asset/Delivered]',
    '📸': '[Screenshot]',
    '📷': '[Photo]',
    '🎥': '[Video]',
    '📹': '[Video Recording]',
    '📄': '[Document/PDF]',
    '📑': '[Document]',
    '💳': '[Card/UPI]',
    '👍': '[Thumbs Up]',
    '👎': '[Thumbs Down]',
    '🎉': '[Success/Done]',
    '🎊': '[Celebration]',
    '🚀': '[Fast/Released]',
    '⭐': '[Rating 5★]',
    '🌟': '[Star]',
    '🙏': '[Thanks/Namaste]',
    '❤️': '[Heart]',
    '🖤': '[Heart]',
    '💙': '[Heart]',
    '💯': '[100% Legit]',
    '🔒': '[Locked/Secure]',
    '🔓': '[Unlocked/Released]',
    '⏳': '[Pending/Waiting]',
    '⌛': '[Hourglass]',
    '⏱️': '[Timer]',
    '⏱': '[Timer]',
    '⏰': '[Alarm/Deadline]',
    '🚨': '[Dispute/Emergency]',
    '⚖️': '[AI Judge/Escrow]',
    '⚖': '[AI Judge/Escrow]',
    '🤖': '[Bot Automated]',
    '👤': '[User]',
    '👥': '[Room Members]',
    '💬': '[Chat Message]',
    '⚡': '[Instant]',
    '🛑': '[Stop/Frozen]',
    '⛔': '[Restricted]',
    '🚫': '[Prohibited]',
    '📞': '[Call]',
    '💎': '[Premium/Elite]',
    '✨': '[Sparkles]',
    '🎯': '[Target/Milestone]',
    '💡': '[Note/Idea]',
    '🔐': '[Key Locked]',
    '🔑': '[Key/Passcode]',
    '📈': '[Volume Up]',
    '📉': '[Volume Down]',
    '🧾': '[Receipt/Bill]',
    '🆔': '[ID/Ref]',
    '🔗': '[Link]',
}


def format_emojis_for_pdf(text: str) -> str:
    """Format emojis and unicode symbols so they render cleanly in ReportLab PDF."""
    if not text:
        return ""
    text = text.replace('\ufe0f', '').replace('\ufe0e', '')
    res = []
    i = 0
    length_text = len(text)
    while i < length_text:
        matched = False
        for sl in (2, 1):
            sub = text[i:i+sl]
            if sub in EMOJI_MAP:
                res.append(f" {EMOJI_MAP[sub]} ")
                i += sl
                matched = True
                break
        if matched:
            continue
        ch = text[i]
        code = ord(ch)
        if code > 127 and (code >= 0x1F000 or code in range(0x2600, 0x27BF) or code in range(0x2300, 0x23FF) or unicodedata.category(ch) in ('So', 'Sk', 'Sm')):
            try:
                name = unicodedata.name(ch).title()
                res.append(f" [{name}] ")
            except Exception:
                res.append(" ")
            i += 1
        elif code < 32 and ch not in ('\n', '\r', '\t'):
            res.append(" ")
            i += 1
        else:
            res.append(ch)
            i += 1
    return re.sub(r' +', ' ', ''.join(res)).strip()


def _ist_time(value) -> str:
    if not value:
        return "Unknown time"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
    except (TypeError, ValueError):
        return str(value)


def _message_text(entry: dict) -> str:
    raw_text = (entry.get("text") or "").strip()
    content_type = (entry.get("content_type") or entry.get("type") or "message").upper()
    file_ref = entry.get("file_ref_id") or ""
    filename = entry.get("file_name") or ""

    parts = []
    if file_ref or content_type not in ("TEXT", "MESSAGE"):
        tag = f"[{content_type}"
        if filename:
            tag += f": {filename}"
        if file_ref:
            tag += f" | Ref #{file_ref}"
        tag += "]"
        parts.append(tag)

    if raw_text:
        parts.append(raw_text)

    full_text = " — ".join(parts) if len(parts) > 1 else (parts[0] if parts else "[No text]")
    return format_emojis_for_pdf(full_text)


def _entries(deal: dict) -> list[dict]:
    try:
        raw = json.loads(deal.get("room_transcript_json") or "[]")
    except (TypeError, ValueError):
        raw = []
    return raw if isinstance(raw, list) else []


def build_transcript_pdf(deal: dict) -> bytes:
    """Build a readable PDF from the append-only transcript stored on a deal."""
    output = BytesIO()
    canvas = Canvas(output, pagesize=A4)
    canvas.setTitle(f"Vault-Pay Deal {deal.get('deal_id', '')} Transcript")
    left = 18 * mm
    right = PAGE_WIDTH - 18 * mm
    usable_width = right - left
    y = PAGE_HEIGHT - 20 * mm

    def write_wrapped(text: str, font: str, size: float, leading: float = 13):
        nonlocal y
        formatted = format_emojis_for_pdf(str(text))
        lines = simpleSplit(formatted, font, size, usable_width) or [""]
        canvas.setFont(font, size)
        for line in lines:
            if y < 20 * mm:
                canvas.showPage()
                y = PAGE_HEIGHT - 20 * mm
                canvas.setFont(font, size)
            canvas.drawString(left, y, line)
            y -= leading

    write_wrapped("VAULT-PAY — PRIVATE DEAL TRANSCRIPT", _BOLD_FONT, 16, 21)
    y -= 2
    write_wrapped(f"Deal #{deal.get('deal_id', 'Unknown')}", _BOLD_FONT, 11, 15)
    write_wrapped(
        f"Buyer: @{deal.get('buyer_un') or deal.get('buyer_id') or '—'}   |   "
        f"Seller: @{deal.get('seller_un') or deal.get('seller_id') or '—'}   |   "
        f"Middleman: @{deal.get('middleman_un') or deal.get('middleman_id') or '—'}",
        _FONT, 9, 13,
    )
    write_wrapped(f"Status: {deal.get('status', 'unknown')}   |   Generated: {_ist_time(datetime.now(timezone.utc).isoformat())}", _FONT, 8.5, 12)
    write_wrapped("Privacy Notice: Attachment files in storage channel auto-purge after 24h for zero data retention.", _FONT, 8.0, 11)
    y -= 5
    canvas.setStrokeColorRGB(0.65, 0.65, 0.65)
    canvas.line(left, y, right, y)
    y -= 14

    entries = _entries(deal)
    if not entries:
        write_wrapped("No room messages were recorded for this deal.", _FONT, 10, 14)
    else:
        for entry in entries:
            role = (entry.get("role") or ("system" if entry.get("type") == "system" else "participant")).upper()
            username = entry.get("username") or entry.get("user_id") or "unknown"
            heading = f"{_ist_time(entry.get('at'))}  |  @{username}  |  {role}"
            write_wrapped(heading, _BOLD_FONT, 9, 12)
            write_wrapped(_message_text(entry), _FONT, 9.5, 13)
            y -= 5

    canvas.save()
    return output.getvalue()
