# Litecoin Auto MM sandbox

This opt-in workflow runs against a private Litecoin Core **regtest** chain. Its coins have no monetary value. Stars checkout and mainnet seller settlement are outside this implementation.

## Run
Use the Python backend (`uvicorn main:app`) and exactly one Telegram polling process. Use a separate test bot, database, empty invite-only supergroups, and a dedicated Litecoin Core regtest wallet. Give the bot invite and pin permissions. The bot and room owner should be the only initial members. Keep these rooms separate from the manual room pool. One room is allocated per deal and retained for delivery, feedback and support. Set these environment variables in your host:
- `AUTO_LTC_REGTEST=1`, `AUTO_LTC_ROOMS` (comma-separated negative room IDs).
- `AUTO_LTC_WALLET` (loaded Core wallet name), `AUTO_LTC_PORT` (local RPC port, default 19443), `AUTO_LTC_RPC_USER`, `AUTO_LTC_RPC_PASSWORD`.
- Configure the existing bot, authorized group and admin test-mode settings as usual. For PostgreSQL deployments, verify database connectivity on startup.
- Optionally configure a secret `AUTO_LTC_NOTIFY_TOKEN`. A local Core walletnotify script may POST to `/internal/ltc-regtest/wallet-event` with a Bearer token from the environment. The endpoint wakes the watcher. The watcher checks Core directly every 10 seconds as a fallback.

## Try a deal
1. From the authorized group, the buyer replies to a seller's message with `/escrow ltc Product description`. Both users start the bot and DM `/ltcjoin DEAL_ID`. Join requests are checked against their Telegram IDs.
2. In the private room the buyer enters `/ltcquote 0.10000000`. The seller checks the product, fee and net amount and accepts. Fund the displayed address from a different regtest wallet. The QR encodes the exact Litecoin URI. Generate two regtest blocks after payment. Partial funding stays pending. Excess funds or unavailable inputs at release go to operator review.
3. The seller delivers in the room and replies to the file with `/ltcdeliver`, or enters `/ltcdeliver reference`. The buyer can request a revision, dispute, or release and confirm.
4. The seller enters `/ltcaddress DEAL_ID rltc1…` in bot DM, reviews the full address and net amount, then confirms. The stored signed transaction can be rebroadcast after a restart. Generate two more blocks to confirm payout.
5. Each party can submit `/ltcrate 1-5 optional feedback` once. Use `/ltcstatus` in the room to refresh the card. Review cases remain held for a test operator. This version has no automated dispute arbitration or refunds.

The sandbox deducts a fixed 5,000-litoshi network fee from seller proceeds, plus the configured platform fee according to the quote. The direct command uses a seller-paid platform fee. The group wizard's INR amount is never converted into LTC. The accepted private LTC quote sets the invoice amount.
