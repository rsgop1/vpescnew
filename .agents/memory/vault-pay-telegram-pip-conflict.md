---
name: Vault-Pay stray telegram pip package
description: A same-named unrelated PyPI package broke python-telegram-bot's import in this project; how it was diagnosed and fixed.
---

On a fresh install of `requirements.txt`, `pip`/the package manager pulled in an unrelated PyPI package literally named `telegram` (v0.0.1, by "liluo", an old Telegram-API wrapper) alongside `python-telegram-bot`. Both packages install a top-level `telegram/` directory, so installing/uninstalling one after the other leaves a corrupted merge — `from telegram import Update` fails with `ImportError: cannot import name 'Update' from 'telegram' (unknown location)`, and `telegram.__file__` is `None` (dangling namespace package).

**Why this matters:** the error looks like a python-telegram-bot version/compat bug, but it's actually a namespace collision from a decoy package with the same import name. Simply uninstalling the decoy isn't enough once files have merged — the real package's files may already be missing from site-packages.

**How it was fixed:** removed both `.dist-info` and the shared `telegram/` directory from site-packages, then reinstalled `python-telegram-bot[job-queue]` fresh (`pip install --target <site-packages>`) so all its files are restored. Also stripped `requirements.txt` of any bare `telegram` entry so it can't be reintroduced.

**How to apply:** if a `python-telegram-bot` project fails to import `telegram.Update` right after a dependency install, check `pip show telegram` for an unexpected non-`python-telegram-bot` owner before debugging version pins.
