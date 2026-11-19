---
name: Manual-middleman admin surface
description: Which operational controls remain visible after Vault-Pay moved to manual-middleman escrow.
---

The active admin surface is manual-middleman operations: deals, disputes, groups, users, registered middlemen, reusable private rooms, and the live admin-only test-mode switch. Legacy wallet/VC data and endpoints may remain for compatibility with old records, but should not be exposed in the active navigation or summaries.

**Why:** The product flow no longer moves buyer funds through the bot wallet; exposing wallet balances, deposits, or withdrawals makes the admin panel suggest a payment path that is not part of the canonical group-card flow.

**How to apply:** When adding admin features, prefer room leases, deal-card state, participant roles, and manual review controls. Treat wallet UI as legacy unless the product direction explicitly reintroduces it.