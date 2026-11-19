---
name: Telegram middleman notification escaping
description: Telegram DM delivery can fail at the entity parser before an inline accept button reaches the middleman.
---

Manual-middleman accept notifications must not depend exclusively on MarkdownV2. Dynamic deal fields such as usernames, product names, and identifiers can contain reserved characters that make Telegram reject the entire message, including its inline keyboard. Use a plain-text template for this critical notification and retain a plain-text retry for other MarkdownV2 DM templates.

**Why:** A valid active middleman and deal flow can look broken when Telegram returns `BadRequest: Can't parse entities`; the send fails before the recipient sees the accept button.

**How to apply:** Keep middleman-accept messages plain text, and make `send_dm` retry parse failures without a parse mode while preserving the reply markup.