# Vault-Pay Agent Instructions

## Read this first

This is an existing production-oriented Telegram escrow project. Treat the
current codebase as the source of truth. Make the smallest safe change that
solves the requested problem.

## Non-negotiable editing rules

1. **Do not rewrite files from scratch.** Make targeted edits to the requested
   functions, components, styles, or configuration only.
2. **Preserve the existing UI, colors, layout, routes, and interaction patterns**
   unless the user explicitly asks for a redesign.
3. **Do not remove or bypass existing Telegram bot logic, database queries,
   escrow verification, payment-state checks, dispute handling, admin
   authorization, or audit logging.**
4. Before changing shared behavior, search for all callers and preserve the
   existing API response shapes unless a breaking change is explicitly
   requested.
5. Do not replace the current stack, migrate the project to another framework,
   or restructure the repository without explicit approval.
6. Do not delete working code just because it is not part of the immediate UI
   task. If code appears unused, explain the finding and leave it in place
   unless removal is specifically approved.
7. Keep diffs focused. Do not make drive-by formatting changes or rename files
   unnecessarily.

## Project architecture

- `bot.py`: Telegram bot handlers, deal flow, payments, escrow state changes,
  notifications, middleman flow, disputes, and scheduled jobs.
- `server.py` / `main.py`: FastAPI application lifecycle and API server. The
  backend also starts and stops the Telegram bot.
- `admin_api.py`: authenticated admin REST API.
- `auth.py`: admin credential verification and JWT handling.
- `db.py`: async PostgreSQL schema initialization and database operations.
- `ai_judge.py`: optional OpenRouter-powered dispute assistance. It must never
  silently replace admin authorization or escrow safety checks.
- `src/`: React/Vite admin dashboard. Preserve the current admin panel visual
  language unless a redesign is requested.
- `config.py`: environment-backed configuration. Never put secrets in source
  files, markdown, commits, browser code, or logs.

## Security and data rules

- Never print, expose, commit, or hardcode `TELEGRAM_BOT_TOKEN`,
  `ADMIN_PASSWORD`, `JWT_SECRET`, `OPENROUTER_API_KEY`, database credentials,
  or any other secret.
- Use Replit Secrets or the host platform's secret manager for sensitive
  values. Ask the user for missing secrets through a secure secret flow; never
  ask them to paste credentials into source code or chat.
- Do not log full Telegram API URLs because they can contain the bot token.
- Never weaken authentication, accept an unverified admin request, or expose
  admin endpoints without `require_admin`.
- Treat Telegram user IDs, group IDs, channel IDs, payment identifiers, and
  deal transcripts as sensitive operational data.
- Do not run destructive database operations, delete deals, wipe users, reset
  balances, or alter production data without explicit user approval.
- Preserve test-mode safeguards. Do not turn test mode off by default.

## Backend and escrow safety

- Preserve the existing three-party/manual-middleman safety model, room
  confirmation checks, dispute freeze behavior, transcript preservation, and
  admin-only resolution paths.
- Validate state transitions on the server. Do not rely on frontend buttons to
  enforce payment, release, refund, dispute, or authorization rules.
- Keep database writes transactional where the current code uses transactions
  or coordinated balance updates.
- When changing a database schema, use backward-compatible initialization or
  migration logic and preserve existing data.
- For any change to bot handlers, check both normal text messages and media
  captions where commands or keywords may be attached to media.
- The bot must continue to use polling only in the intended single runtime;
  do not create a second polling process accidentally.
- Private deal room workflow uses dedicated action-button messages (`send_or_update_room_flow`)
  to guide Buyer, Seller, and Middleman through UPI payment, receipt confirmation, asset delivery,
  double-confirmation release, seller payout, and final completion.
- Deal creation flow supports Step 6 Middleman Mode selection: Manual Middleman (moderated private
  room) and Auto Middleman (instant automated routing option for future automated escrows).

## Frontend rules

- Use the existing React/Vite conventions and API client.
- Keep API calls same-origin and relative (`/api/...`) so preview and deployed
  environments work consistently.
- Preserve authentication redirects, loading states, error states, and
  destructive-action confirmations.
- Do not put secrets in frontend code or local storage. Only the existing
  admin session token may be handled according to the current auth design.
- When changing an admin action, verify the corresponding backend endpoint and
  authorization behavior too; a visual-only change is not enough.

## Workflow and verification

The project uses:

- Backend: `uvicorn main:app --host 0.0.0.0 --port 8000 --reload`
- Admin preview: `npm run dev` on port `5000`
- Frontend build: `npm run build`

After meaningful changes:

1. Check the smallest relevant code path first.
2. Run `npm run build` for frontend changes.
3. Restart the affected workflow after backend, dependency, or configuration
   changes.
4. Check workflow logs for startup errors and secret leakage.
5. Verify `/api/health`, admin login, and the affected authenticated API path.
6. For UI changes, inspect the running preview at desktop and mobile widths.
7. Report anything that still needs real Telegram or payment-environment
   testing instead of claiming it was verified locally.

## Change summary requirement

When you finish a change, report:

- what was changed;
- which files were touched;
- what checks were run;
- what was not tested because it requires real Telegram, payment, or
  production access.
