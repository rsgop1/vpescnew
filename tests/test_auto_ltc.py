import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import auto_ltc as ltc
import auto_ltc_bot as flow
import db


class PrivateDealTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for name, value in [
            ("__file__", str(Path(temp.name) / "db.py")),
            ("_pool", None),
            ("_sqlite_conn", None),
            ("DATABASE_URL", ""),
        ]:
            self.enterContext(patch.object(db, name, value))
        await ltc.init()
        self.addAsyncCleanup(db._sqlite_conn.close)
        self.bot = AsyncMock()
        self.bot.username = "sandboxbot"
        self.ctx = SimpleNamespace(bot=self.bot)
        self.row = await ltc.create(
            "a1",
            -11,
            dict(
                stage="delivered",
                buyer=1,
                seller=2,
                public=-22,
                public_msg=12,
                product="file",
                terms="v2",
                card=31,
                delivery="room message 10",
                address="regtest-placeholder",
                **ltc.quote_amount(dict(pct="2", payer="seller"), ltc.units("0.1")),
            ),
        )

    async def click(self, row, action, user, chat):
        q = SimpleNamespace(
            data=f"lt:a1:{row['rev']}:{action}",
            from_user=SimpleNamespace(id=user),
            message=SimpleNamespace(chat=SimpleNamespace(id=chat)),
            answer=AsyncMock(),
        )
        await flow.callback(SimpleNamespace(callback_query=q), self.ctx)
        return await ltc.get("a1")

    async def test_release_and_destination_are_separate_approvals(self):
        r = await self.click(self.row, "release", 2, -11)
        self.assertEqual(r["stage"], "delivered")
        r = await self.click(r, "release", 1, -22)
        self.assertEqual(r["stage"], "delivered")
        r = await self.click(r, "release", 1, -11)
        self.assertEqual(r["stage"], "release")
        stale = await self.click(self.row, "release", 1, -11)
        self.assertEqual(stale["rev"], r["rev"])
        r = await self.click(r, "confirm", 1, -11)
        self.assertEqual(r["stage"], "destination")
        r = await ltc.change(r, stage="verify", destination="rltc1-example")
        held = await self.click(r, "pay", 2, -11)
        self.assertEqual(held["stage"], "verify")
        paid = await self.click(r, "pay", 2, 2)
        self.assertEqual(paid["stage"], "payout")
        self.assertEqual(paid["net"], 9795000)
        self.assertEqual((await self.click(r, "pay", 2, 2))["rev"], paid["rev"])

    async def test_hold_wins_over_old_release(self):
        r = await self.click(self.row, "dispute", 2, -11)
        self.assertEqual(r["stage"], "review")
        self.assertEqual(
            (await self.click(self.row, "release", 1, -11))["stage"], "review"
        )
        self.assertIsNone(await ltc.create("a2", -11, {}))

    async def test_excess_payment_is_held(self):
        class Wallet:
            async def rpc(self, method, *args):
                return 0.11 if method == "getreceivedbyaddress" else []

        r = await ltc.change(self.row, stage="waiting")
        self.assertEqual((await ltc.advance(Wallet(), r))["stage"], "review")

    async def test_each_party_can_rate_once(self):
        await ltc.change(self.row, stage="completed")
        msg = SimpleNamespace(
            text="/ltcrate 5 Good", chat_id=-11, reply_text=AsyncMock()
        )
        update = SimpleNamespace(
            effective_message=msg, effective_user=SimpleNamespace(id=1)
        )
        self.ctx.args = ["5", "Good"]
        await flow.command(update, self.ctx)
        self.ctx.args = ["1", "Replacement"]
        await flow.command(update, self.ctx)
        self.assertEqual(
            (await ltc.get("a1"))["ratings"]["1"], {"score": 5, "feedback": "Good"}
        )

    def test_amount_precision(self):
        for value in ["-1", "0.000000001", "nan", "1e3", "100.00000001"]:
            with self.assertRaises(ValueError):
                ltc.units(value)
