---
name: Manual middleman room safety
description: Durable rules for private-room confirmations, disputes, transcripts, and off-platform settlement decisions.
---

Private middleman rooms are a safety and evidence boundary, not a payment processor. All three assigned roles must confirm before the room enters the proceed phase; the room must announce that transition, and the private room must never be able to unilaterally complete a deal. Disputes freeze actions, preserve the transcript, quarantine the room, and require an admin-recorded decision.

**Why:** Manual UPI/cash settlement happens outside the bot, so an automatic “completed” state or wallet movement can falsely claim that money was transferred. The admin needs an independent, preserved record for buyer, seller, and middleman disputes.

**How to apply:** Keep participant permissions and callback authorization aligned with the persisted deal state. Treat transcript/media metadata and admin resolution notes as audit evidence. Add real payment-proof capture and state-machine tests before moving toward auto-middleman.