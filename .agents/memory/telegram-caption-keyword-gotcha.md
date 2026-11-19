---
name: Telegram caption vs text commands
description: A text keyword sent as a photo's caption is invisible to text-only message handlers.
---

python-telegram-bot (and Telegram in general) treats a photo message's caption as a separate
field from a plain text message. If your bot has a text handler that watches for a keyword
(e.g. "done", "cancel", "/confirm"-style free text), a user who attaches that same word as the
caption on a photo will silently bypass it — the photo handler and the text handler are two
different code paths, and only the text handler usually gets caption-checking logic added
after the fact.

**Why:** this caused a real bug in Vault-Pay where dispute evidence collection never closed
because "done" arrived as a photo caption instead of a standalone message.

**How to apply:** wherever a text keyword can plausibly arrive attached to media (captions on
photos/videos/documents), explicitly check `message.caption` in the media handler too, not just
`message.text` in the text handler.
