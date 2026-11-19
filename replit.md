# Vault-Pay Escrow

A Telegram escrow/wallet bot with a React admin dashboard.

## Architecture
- **Backend** (port 8000): FastAPI (`server.py` / `main.py`) + python-telegram-bot (`bot.py`), running together via FastAPI's lifespan. PostgreSQL via `DATABASE_URL` (Replit-managed).
- **Frontend** (port 5000): React + Vite admin panel (`src/`, `*.jsx` files), proxies `/api` to the backend on 8000.

## Running
- `Backend` workflow: `uvicorn main:app --host 0.0.0.0 --port 8000 --reload`
- `Start application` workflow: `npm run dev` (Vite on port 5000, shown in the preview pane)

## Secrets / config (see `config.py`)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_ID`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_ADMIN_ID` — Telegram bot wiring
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — web admin login
- `JWT_SECRET` — admin session signing key
- `ADMIN_UPI_ID` / `ADMIN_LTC_ADDRESS` — payout destinations shown to users
- `DATABASE_URL` — Postgres connection (provided automatically by Replit)
- `reportlab` — PDF generation for preserved deal-room transcripts

## Deal transcripts
- A completed or admin-cleaned middleman room sends a PDF transcript to the
  buyer, seller, middleman, and Telegram admin before room messages are removed.
- All transcript timestamps are rendered in IST (`Asia/Kolkata`).
- The Telegram admin can request any preserved deal transcript privately with
  `/transcript DEAL_ID` (for example, `/transcript 4092`).

## User preferences
- Keep responses efficient; avoid unnecessary iteration/credit use.

## Status
- Set up on 2026-09-23: Python and Node dependencies installed, required credentials saved in Replit Secrets, both workflows running, Telegram polling confirmed live as `@rsgescrow_bot`, and admin login/API access verified.
