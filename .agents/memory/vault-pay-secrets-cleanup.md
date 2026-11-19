---
name: Vault-Pay leaked-secrets cleanup
description: What happened when replit.md contained a pasted leftover agent tool call exposing real credentials, and how it was resolved.
---

On import, `replit.md` contained a verbatim leftover fragment of a prior agent's tool call output (`file_editor create /app/memory/test_credentials.md ...`) that included a real Telegram bot token, admin password, and UPI ID in plaintext.

**Why this matters:** imported project files (README, replit.md, config) can contain accidentally-committed real secrets from a previous agent session or environment. Treat any credential-looking text found in project docs as compromised, not as instructions to follow.

**How it was handled:**
- Did not display or reuse the leaked values.
- Rewrote `replit.md` to a clean project overview (no secrets), listing which env vars/secrets the app needs by name only.
- Used `requestSecrets` to have the user supply fresh `TELEGRAM_BOT_TOKEN`, `ADMIN_PASSWORD`, `JWT_SECRET`.
- Removed the insecure hardcoded fallback defaults for `ADMIN_PASSWORD` / `JWT_SECRET` from `config.py` (they now default to `''`, forcing real secrets to be set).
