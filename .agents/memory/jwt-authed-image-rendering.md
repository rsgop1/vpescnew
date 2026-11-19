---
name: JWT-authed image rendering
description: How to render an image behind Bearer-token auth in a React admin panel.
---

A backend endpoint gated by `Authorization: Bearer <jwt>` cannot be used directly as an `<img src="...">`
— the browser's image loader doesn't attach custom headers, so the request 401s and the image never
renders (this was the actual root cause of "screenshots don't show up" bugs, not a data pipeline issue).

**Why:** discovered in Vault-Pay's admin dashboard — dispute/deposit evidence photos were fully captured
and stored server-side (correct `file_id`, correct DB writes), but the frontend rendered `—` because the
image tag had no way to authenticate.

**How to apply:** fetch the image via the app's existing authed HTTP client with
`responseType: 'blob'`, then `URL.createObjectURL(blob)` and put that into the `<img src>`. Revoke the
object URL on unmount to avoid leaking memory. Don't try to pass the token as a query param unless the
backend explicitly supports that — prefer the blob approach so the existing auth story doesn't change.
