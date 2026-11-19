---
name: Telegram private-room pool
description: Privacy and lifecycle constraints for reusable Telegram middleman rooms.
---

Telegram forum topics do not provide a reliable per-topic membership ACL. Sensitive buyer/seller/middleman conversations should use separately registered private groups, short-lived role-specific invite links, and server-side expected-user checks.

**Why:** A reused room can leak the previous deal if membership, invite links, or message history are not cleared completely. Telegram cannot erase screenshots users already took, and cleanup APIs can fail or be limited by message age/permissions.

**How to apply:** Treat each room as leased capacity, not simply an empty chat. Mark it occupied only after an atomic assignment, record the deal transcript, remove expected participants, delete recorded messages, and return it to `available` only after cleanup succeeds; otherwise quarantine it for admin review.