"""Private Litecoin regtest deals. All wallet RPC calls are restricted to local regtest."""

import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import httpx
import db

COIN = 100_000_000
MINER_FEE = 5000  # Fixed sandbox transaction fee, disclosed before acceptance.
CONFIRMATIONS = 2


def units(text):
    if not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,8})?", text):
        raise ValueError("Enter a decimal LTC amount with up to eight decimal places.")
    value = int(Decimal(text) * COIN)
    if not 1_000_000 <= value <= 100 * COIN:
        raise ValueError("Use 0.01 to 100 test LTC.")
    return value


def fmt(value):
    return f"{Decimal(value) / COIN:.8f}"


def quote_amount(row, value):
    pct = Decimal(str(row["pct"]))
    if not pct.is_finite() or not 0 <= pct <= 100:
        raise ValueError("Invalid fee setting.")
    fee = int((value * pct / 100).quantize(1, rounding=ROUND_HALF_UP))
    buyer_fee = {"buyer": fee, "split": fee // 2, "seller": 0}[row["payer"]]
    net = value - (fee - buyer_fee) - MINER_FEE
    if net < 10000 or 0 < fee < 10000:
        raise ValueError("Choose a larger amount for this fee rate.")
    return dict(amount=value, gross=value + buyer_fee, fee=fee, net=net)


def enabled():
    return os.environ.get("AUTO_LTC_REGTEST") == "1"


def rooms():
    return [
        int(x)
        for x in os.environ.get("AUTO_LTC_ROOMS", "").split(",")
        if re.fullmatch(r"-[0-9]+", x.strip())
    ]


async def init():
    pool = await db.get_pool()
    async with pool.acquire() as c:
        await c.execute("""CREATE TABLE IF NOT EXISTS auto_ltc_deals (
            id TEXT PRIMARY KEY, room BIGINT NOT NULL UNIQUE,
            revision INTEGER NOT NULL DEFAULT 0, published INTEGER NOT NULL DEFAULT -1, data TEXT NOT NULL)""")


def unpack(record):
    return dict(
        json.loads(record["data"]),
        id=record["id"],
        room=record["room"],
        rev=record["revision"],
        published=record["published"],
    )


async def create(key, room, data):
    pool = await db.get_pool()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO auto_ltc_deals(id,room,data) VALUES($1,$2,$3)
            ON CONFLICT DO NOTHING RETURNING *""",
            key,
            room,
            json.dumps(data),
        )
    return unpack(row) if row else None


async def get(key):
    pool = await db.get_pool()
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM auto_ltc_deals WHERE id=$1", key)
    return unpack(row) if row else None


async def change(row, **changes):
    data = {k: v for k, v in row.items() if k not in ("id", "room", "rev", "published")}
    data.update(changes)
    pool = await db.get_pool()
    async with pool.acquire() as c:
        saved = await c.fetchrow(
            """UPDATE auto_ltc_deals SET revision=revision+1, data=$1
            WHERE id=$2 AND revision=$3 RETURNING *""",
            json.dumps(data),
            row["id"],
            row["rev"],
        )
    if not saved:
        raise ValueError("Deal updated. Open the latest card.")
    return unpack(saved)


async def mark_published(row):
    pool = await db.get_pool()
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE auto_ltc_deals SET published=$1 WHERE id=$2 AND revision=$1",
            row["rev"],
            row["id"],
        )


async def all_rows():
    pool = await db.get_pool()
    async with pool.acquire() as c:
        return [unpack(r) for r in await c.fetch("SELECT * FROM auto_ltc_deals")]


class Wallet:
    def __init__(self, client, url):
        self.client = client
        self.url = url

    async def rpc(self, method, *args):
        r = await self.client.post(
            self.url, json={"method": method, "params": list(args), "id": 1}
        )
        result = r.json()
        if result.get("error"):
            raise RuntimeError(f"Litecoin RPC {method} error {result['error']['code']}")
        r.raise_for_status()
        return result["result"]

    async def prepare(self, row, inputs):
        # Spend only this deal's confirmed outputs. All amounts use integer litoshis.
        outputs = {row["destination"]: fmt(row["net"])}
        if row["fee"]:
            outputs[await self.rpc("getrawchangeaddress", "bech32")] = fmt(row["fee"])
        raw = await self.rpc(
            "createrawtransaction",
            [dict(txid=u["txid"], vout=u["vout"]) for u in inputs],
            outputs,
        )
        signed = await self.rpc("signrawtransactionwithwallet", raw)
        if not signed["complete"]:
            raise RuntimeError("Incomplete regtest wallet signature")
        tx = await self.rpc("decoderawtransaction", signed["hex"])
        return await change(row, stage="sending", raw=signed["hex"], txid=tx["txid"])


async def wallet_call(action, *args):
    if not enabled():
        raise ValueError("LTC sandbox is disabled.")
    name = os.environ["AUTO_LTC_WALLET"]
    port = int(os.environ.get("AUTO_LTC_PORT", "19443"))
    url = f"http://127.0.0.1:{port}/wallet/{quote(name, safe='')}"
    auth = (os.environ["AUTO_LTC_RPC_USER"], os.environ["AUTO_LTC_RPC_PASSWORD"])
    async with httpx.AsyncClient(auth=auth, trust_env=False, timeout=8) as client:
        wallet = Wallet(client, url)
        if (await wallet.rpc("getblockchaininfo"))["chain"] != "regtest":
            raise ValueError("A Litecoin regtest wallet is required.")
        return await action(wallet, *args)


async def advance(wallet, row):
    stage = row["stage"]
    if stage in ("waiting", "confirming", "payout"):
        addr = row["address"]
        received = (
            Decimal(str(await wallet.rpc("getreceivedbyaddress", addr, 0))) * COIN
        )
        coins = await wallet.rpc("listunspent", 0, 9999999, [addr])
        confirmed = [u for u in coins if u["confirmations"] >= CONFIRMATIONS]
        balance = sum(int(Decimal(str(u["amount"])) * COIN) for u in confirmed)
        if received > row["gross"] or (stage == "payout" and balance != row["gross"]):
            return await change(row, stage="review")
        if stage == "payout":
            return await wallet.prepare(row, confirmed)
        status = (
            "funded"
            if balance == row["gross"]
            else "confirming"
            if received
            else "waiting"
        )
        if status != stage or row.get("received", 0) != int(received):
            return await change(row, stage=status, received=int(received))
    elif stage == "sending":
        try:
            tx = await wallet.rpc("gettransaction", row["txid"])
        except RuntimeError as err:
            if not str(err).endswith("error -5"):
                raise
            await wallet.rpc("sendrawtransaction", row["raw"])
            tx = {"confirmations": 0}
        if tx["confirmations"] < 0:
            return await change(row, stage="review")
        return await change(row, stage="sent", raw=None)
    elif stage == "sent":
        tx = await wallet.rpc("gettransaction", row["txid"])
        if tx["confirmations"] < 0:
            return await change(row, stage="review")
        if tx["confirmations"] >= CONFIRMATIONS:
            return await change(row, stage="completed")
    return row
