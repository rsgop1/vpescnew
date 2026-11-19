---
name: Vault-Pay Architecture
description: FastAPI+Bot on port 8000 (console), React Vite on port 5000 (webview); PostgreSQL via DATABASE_URL; bot runs inside FastAPI lifespan.
---

- FastAPI (`main.py`) hosts both the admin REST API and the Telegram bot (`bot.py`), started inside its lifespan — port 8000, no visible preview.
- React admin panel (Vite) is the actual preview surface — port 5000, calls `/api/admin/*`.
- `db.py` is the single data-access layer; `ALTER TABLE ADD COLUMN IF NOT EXISTS` in `init_db()` is the pattern for schema migrations (no separate migration tool).
- Dispute handling is manual-only: the admin panel reviews the ticket/evidence and chooses release, refund, or split; unrelated AI risk and deposit-verification helpers remain separate.
