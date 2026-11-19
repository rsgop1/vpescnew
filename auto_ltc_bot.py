"""Telegram interface for isolated Litecoin regtest deals."""

import asyncio
import io
import logging
import secrets
from datetime import datetime, timedelta, timezone
from html import escape

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard
from telegram.error import BadRequest, TelegramError
from telegram.ext import CallbackQueryHandler, ChatJoinRequestHandler, CommandHandler

import auto_ltc as ltc
import db

log = logging.getLogger(__name__)
STEPS = {
    "quote": "Buyer · enter the test LTC amount with /ltcquote",
    "terms": "Seller · check the terms and accept the quote",
    "waiting": "Buyer · send the exact amount on Litecoin regtest",
    "confirming": "Payment detected · waiting for blockchain confirmations",
    "funded": "✅ Payment received. Seller can deliver the product to the buyer.",
    "delivered": "Buyer · inspect the delivery before releasing payment",
    "release": "Buyer · confirm release only after checking the product",
    "destination": "Seller · enter your regtest payout address in bot DM",
    "verify": "Seller · review and confirm the destination in bot DM",
    "payout": "Payout authorized · preparing transaction",
    "sending": "Submitting the signed payout",
    "sent": "📤 Payment sent · waiting for confirmations",
    "completed": "✅ Payment confirmed · deal closed. Both parties can rate the deal.",
    "review": "⚖️ On hold for operator review",
}
BUTTONS = {
    "terms": [("Accept quote", "accept")],
    "waiting": [("Show QR", "qr")],
    "confirming": [("Show QR", "qr")],
    "funded": [("📦 Delivery instructions", "instructions")],
    "delivered": [("🔓 Release payment", "release"), ("Request revision", "revision")],
    "release": [("Confirm release", "confirm"), ("Go back", "back")],
    "destination": [("Enter payout details privately", "dm")],
    "verify": [("Review payout privately", "dm")],
}


def render(r):
    key = r["id"]
    text = [
        f"<b>Litecoin regtest · Deal #{key}</b>",
        escape(r["product"]),
        escape(r["terms"]),
        f"<b>{STEPS[r['stage']]}</b>",
        f"Payment ID · <code>PAY-{key}</code>",
    ]
    if r["stage"] == "quote":
        text.append(
            f"Buyer: enter the agreed LTC quote, e.g. <code>/ltcquote 0.10000000</code>."
        )
    if "gross" in r:
        for title, amount in [
            ("Buyer total", r["gross"]),
            ("Platform fee", r["fee"]),
            ("Network fee", ltc.MINER_FEE),
            ("Seller receives", r["net"]),
        ]:
            text.append(f"{title} · <code>{ltc.fmt(amount)} LTC</code>")
    if r["stage"] in ("waiting", "confirming"):
        text.append(f"Regtest address · <code>{r['address']}</code>")
        if r.get("received"):
            text.append(f"Detected · {ltc.fmt(r['received'])} LTC")
    if r.get("delivery"):
        text.append("Delivery · " + escape(r["delivery"]))
    if r.get("txid"):
        text.append(f"Payout ID · <code>{r['txid']}</code>")
    actions = list(BUTTONS.get(r["stage"], []))
    if r["stage"] in (
        "waiting",
        "confirming",
        "funded",
        "delivered",
        "release",
        "destination",
        "verify",
    ):
        actions.append(("⚖️ Open dispute", "dispute"))
    buttons = [
        [Button(label, callback_data=f"lt:{key}:{r['rev']}:{act}")]
        for label, act in actions
    ]
    if r["stage"] == "completed":
        text.append("Use <code>/ltcrate 5 optional feedback</code> in this room.")
    return "\n".join(text), Keyboard(buttons) if buttons else None


async def edit(bot, chat, msg, **kw):
    try:
        await bot.edit_message_text(chat_id=chat, message_id=msg, **kw)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


_publish_lock = asyncio.Lock()


async def show(bot, r):
    async with _publish_lock:
        current = await ltc.get(r["id"])
        await publish(bot, current)


async def publish(bot, r):
    if not r.get("card"):
        m = await bot.send_message(r["room"], f"Opening regtest deal #{r['id']}…")
        r = await ltc.change(r, card=m.message_id)
        try:
            await bot.pin_chat_message(
                r["room"], m.message_id, disable_notification=True
            )
        except TelegramError:
            pass
    text, buttons = render(r)
    await edit(
        bot, r["room"], r["card"], text=text, reply_markup=buttons, parse_mode="HTML"
    )
    public = f"Litecoin sandbox · Deal #{r['id']} · {r['stage'].replace('_', ' ').title()}\nParticipants: open @{bot.username} and use /ltcjoin {r['id']} in DM."
    await edit(bot, r["public"], r["public_msg"], text=public, reply_markup=None)
    await ltc.mark_published(r)


async def create_draft(context, d, msg):
    if not ltc.enabled():
        await msg.edit_text(
            "Auto MM sandbox is disabled. Start /escrow for a manual deal."
        )
        return
    buyer, seller = d["initiator_id"], d["counterparty_id"]
    if d["role"] != "buyer":
        buyer, seller = seller, buyer
    if buyer == seller:
        await msg.edit_text("Buyer and seller must be different accounts.")
        return
    import bot as legacy

    pct = await legacy.get_fee_pct()
    key = secrets.token_hex(5)
    for room in ltc.rooms():
        if room == msg.chat_id or await db.get_middleman_room(room):
            continue
        chat = await context.bot.get_chat(room)
        if (
            chat.username
            or chat.type != "supergroup"
            or await context.bot.get_chat_member_count(room) > 2
        ):
            continue
        r = await ltc.create(
            key,
            room,
            dict(
                stage="quote",
                product=d["product_name"],
                terms=d.get("toc") or "",
                pct=str(pct),
                payer=d.get("fee_payer", "buyer"),
                buyer=buyer,
                seller=seller,
                inr=d["amount"],
                public=msg.chat_id,
                public_msg=msg.message_id,
            ),
        )
        if r:
            await show(context.bot, r)
            return
    await msg.edit_text(
        "No fresh private sandbox room is available. Ask the operator to add one, then retry /escrow."
    )


async def join(update, ctx):
    msg = update.effective_message
    if msg.chat.type != "private" or len(ctx.args) != 1:
        return
    r = await ltc.get(ctx.args[0])
    if not r or update.effective_user.id not in (r["buyer"], r["seller"]):
        await msg.reply_text("No matching participant deal.")
        return
    link = await ctx.bot.create_chat_invite_link(
        r["room"],
        creates_join_request=True,
        expire_date=datetime.now(timezone.utc) + timedelta(minutes=20),
    )
    await msg.reply_text(
        "Join your private regtest deal room.",
        reply_markup=Keyboard([[Button("Open room", url=link.invite_link)]]),
    )


async def join_request(update, ctx):
    req = update.chat_join_request
    if req.chat.id not in ltc.rooms():
        return
    r = await room_row(req.chat.id)
    if r and req.from_user.id in (r["buyer"], r["seller"]):
        await req.approve()
    else:
        await req.decline()


async def room_row(room):
    pool = await db.get_pool()
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM auto_ltc_deals WHERE room=$1", room)
    return ltc.unpack(row) if row else None


async def command(update, ctx):
    msg = update.effective_message
    cmd = msg.text.split()[0].split("@")[0]
    try:
        if cmd == "/ltcaddress":
            if msg.chat.type != "private" or len(ctx.args) != 2:
                raise ValueError("In DM use /ltcaddress DEAL_ID REGTEST_ADDRESS")
            r = await ltc.get(ctx.args[0])
        else:
            r = await room_row(msg.chat_id)
        user = update.effective_user.id
        if not r or user not in (r["buyer"], r["seller"]):
            raise ValueError("This command is for the deal participants.")
        if cmd == "/ltcstatus":
            await show(ctx.bot, r)
            return
        if (
            cmd == "/ltcquote"
            and r["stage"] == "quote"
            and user == r["buyer"]
            and len(ctx.args) == 1
        ):
            r = await ltc.change(
                r, stage="terms", **ltc.quote_amount(r, ltc.units(ctx.args[0]))
            )
        elif cmd == "/ltcdeliver" and r["stage"] == "funded" and user == r["seller"]:
            ref = " ".join(ctx.args)
            if msg.reply_to_message:
                ref = f"Room message #{msg.reply_to_message.message_id} " + ref
            if not ref:
                raise ValueError(
                    "Reply to the delivered file with /ltcdeliver or include a delivery reference."
                )
            r = await ltc.change(r, stage="delivered", delivery=ref[:500])
        elif (
            cmd == "/ltcaddress"
            and r["stage"] in ("destination", "verify")
            and user == r["seller"]
        ):
            dest = ctx.args[1]

            async def validate(w):
                return await w.rpc("validateaddress", dest)

            if (
                not dest.startswith("rltc1")
                or not (await ltc.wallet_call(validate))["isvalid"]
                or dest == r["address"]
            ):
                raise ValueError(
                    "Use the seller's own Litecoin regtest bech32 address."
                )
            r = await ltc.change(r, destination=dest, stage="verify")
            await payout_prompt(ctx.bot, r)
        elif (
            cmd == "/ltcrate"
            and r["stage"] == "completed"
            and ctx.args
            and ctx.args[0] in ("1", "2", "3", "4", "5")
        ):
            ratings = dict(r.get("ratings", {}))
            if str(user) in ratings:
                raise ValueError("Your rating was already saved.")
            ratings[str(user)] = {
                "score": int(ctx.args[0]),
                "feedback": " ".join(ctx.args[1:])[:400],
            }
            r = await ltc.change(r, ratings=ratings)
            await msg.reply_text("Your rating was saved.")
        else:
            raise ValueError(
                "Check the current step, your role and the command arguments."
            )
        await show(ctx.bot, r)
    except ValueError as e:
        await msg.reply_text(str(e))


async def payout_prompt(bot, r):
    await bot.send_message(
        r["seller"],
        f"<b>Confirm regtest payout</b>\nDeal #{r['id']}\n"
        f"Destination · <code>{escape(r['destination'])}</code>\nNet · <code>{ltc.fmt(r['net'])} LTC</code>",
        parse_mode="HTML",
        reply_markup=Keyboard(
            [[Button("Confirm payout", callback_data=f"lt:{r['id']}:{r['rev']}:pay")]]
        ),
    )


async def callback(update, ctx):
    q = update.callback_query
    _, key, rev, action = q.data.split(":")
    r = await ltc.get(key)
    user = q.from_user.id
    try:
        if not r or user not in (r["buyer"], r["seller"]) or int(rev) != r["rev"]:
            raise ValueError("Use the latest deal card or /ltcstatus.")
        if q.message.chat.id != (r["seller"] if action == "pay" else r["room"]):
            raise ValueError("Open this action in the correct chat.")
        stage = r["stage"]
        role = "buyer" if user == r["buyer"] else "seller"
        if action == "accept" and (stage, role) == ("terms", "seller"):

            async def address(w):
                return await w.rpc("getnewaddress", key, "bech32")

            addr = await ltc.wallet_call(address)
            if not addr.startswith("rltc1"):
                raise ValueError("Expected a regtest address.")
            r = await ltc.change(r, stage="waiting", address=addr)
        elif action == "qr" and stage in ("waiting", "confirming") and role == "buyer":
            import qrcode

            out = io.BytesIO()
            qrcode.make(f"litecoin:{r['address']}?amount={ltc.fmt(r['gross'])}").save(
                out, format="PNG"
            )
            out.seek(0)
            await ctx.bot.send_photo(
                r["room"],
                out,
                caption=f"Regtest only · PAY-{key} · {ltc.fmt(r['gross'])} LTC",
            )
        elif action == "instructions" and (stage, role) == ("funded", "seller"):
            await q.message.reply_text(
                "Send the product here, then reply to it with /ltcdeliver."
            )
        elif action == "dm" and stage in ("destination", "verify") and role == "seller":
            if stage == "verify":
                await payout_prompt(ctx.bot, r)
            else:
                await ctx.bot.send_message(
                    user,
                    f"Deal #{key}. Reply with /ltcaddress {key} YOUR_REGTEST_ADDRESS",
                )
        elif action == "dispute" and stage in (
            "waiting",
            "confirming",
            "funded",
            "delivered",
            "release",
            "destination",
            "verify",
        ):
            r = await ltc.change(r, stage="review", previous=stage, opened_by=user)
        else:
            next_stage = {
                ("release", "delivered", "buyer"): "release",
                ("revision", "delivered", "buyer"): "funded",
                ("back", "release", "buyer"): "delivered",
                ("confirm", "release", "buyer"): "destination",
                ("pay", "verify", "seller"): "payout",
            }.get((action, stage, role))
            if not next_stage:
                raise ValueError(
                    "This action belongs to the other participant or a different step."
                )
            r = await ltc.change(r, stage=next_stage)
        await q.answer()
        await show(ctx.bot, r)
    except ValueError as e:
        await q.answer(str(e), show_alert=True)
    except (TelegramError, RuntimeError, OSError) as e:
        log.warning("Sandbox action %s needs retry (%s)", action, type(e).__name__)
        await q.message.reply_text(
            "Action could not finish. Open the bot in DM, then use /ltcstatus in the room."
        )


async def monitor(ctx):
    for r in await ltc.all_rows():
        try:
            after = r
            if r["stage"] in ("waiting", "confirming", "payout", "sending", "sent"):
                after = await ltc.wallet_call(ltc.advance, r)
            if after["rev"] != after["published"]:
                await show(ctx.bot, after)
        except Exception as e:
            log.warning("Regtest update %s will retry (%s)", r["id"], type(e).__name__)


async def register(app):
    await ltc.init()
    app.add_handler(CommandHandler("ltcjoin", join))
    app.add_handler(
        CommandHandler(
            ["ltcquote", "ltcstatus", "ltcdeliver", "ltcaddress", "ltcrate"], command
        )
    )
    app.add_handler(CallbackQueryHandler(callback, pattern=r"^lt:"))
    app.add_handler(ChatJoinRequestHandler(join_request))
    app.job_queue.run_repeating(monitor, interval=10, first=2)
