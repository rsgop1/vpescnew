---
name: Vault-Pay agentic guardrails
description: Design approach for abuse detection, auto-hold, and identity risk features in the escrow bot.
---

Vault-Pay's fraud-reduction features (abuse/profanity detection, Auto-Hold Guardian mid-deal scam-signal
watch, Identity Risk Check) are deliberately rule-based/regex-first rather than LLM-per-message.

**Why:** these checks run on every chat message in active relays; an LLM round-trip per message would be
slow, costly, and subject to OpenRouter rate limits. Regex/heuristic checks are instant and free, and are
"good enough" as a first-pass signal that gets a human (admin) in the loop rather than trying to be a
perfect autonomous judge.

**How to apply:**
- Per-message checks (abuse keywords, scam-signal phrases, identity heuristics) stay rule-based.
- Reserve OpenRouter/LLM calls for one-shot generative helpers that run once per deal/dispute, not once
  per message — e.g. generating a tailored evidence checklist when a dispute opens, or a deal-specific
  safety briefing when a deal goes active.
- None of these guardrails auto-block or auto-punish irreversibly on their own signal: abuse warnings
  accumulate before any account-level flag, Auto-Hold only pauses fund release (never reverses funds) and
  always alerts the admin with reasoning + a one-click clear button, and Identity Risk Check only adds a
  visible flag for the admin / a caution DM to the parties — it never blocks a deal outright. This mirrors
  the existing AI dispute judge's own design constraint (never auto-reverse funds, escalate on uncertainty).
- Telegram user IDs are roughly monotonically increasing, so a very high numeric ID is a cheap (imperfect)
  proxy for "new account" when no better signal (join date, etc.) is available via the Bot API.
