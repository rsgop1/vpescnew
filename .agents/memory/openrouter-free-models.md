---
name: OpenRouter free-model gotchas
description: Practical notes on picking a working OpenRouter model/key when the user hasn't added billing credits.
---

When a project calls OpenRouter with a user-supplied `OPENROUTER_API_KEY` that has no purchased credits:

- Paid models (e.g. `openai/gpt-4o`, most non-`:free` ids) return **402 Payment Required** immediately, even for a single small request.
- Many `:free`-suffixed models return **429** ("temporarily rate-limited upstream") — this is an upstream provider limit, not the user's own quota, and varies minute to minute.

**How to apply:** before hardcoding a default model for a user without confirmed billing, query `GET https://openrouter.ai/api/v1/models` with their key and filter for `id.endswith(':free')` (and `architecture.input_modalities` containing `image` if vision is needed), then smoke-test a couple of candidates with a trivial completion call and keep the first that returns 200. Make the model configurable via an env var so the user can upgrade later without a code change.
