# VAULT-PAY ESCROW PLATFORM: OFFICIAL KNOWLEDGE BASE & SYSTEM MANUAL

> Version: 3.2.0 (Institutional Production Grade)  
> Classification: Confidential Operational Specification & AI Context Repository  
> Last Updated: 2026-10-01  

---

## 1. PLATFORM OVERVIEW & CORE PHILOSOPHY

Vault-Pay is a high-security, institutional-grade Telegram escrow infrastructure designed for digital asset trades, social media accounts, domain transfers, freelance services, and cryptocurrency/fiat transactions.

### Core Tenets:
1. **Zero-Trust Escrow Safety**: Neither buyer nor seller holds unilateral power over funds. Funds remain locked in the multi-signatory Vault until delivery criteria are met or an authorized arbitrator settles a dispute.
2. **100% Protection to Innocent Parties**: Vault-Pay rejects arbitrary partial "split" rulings. If a seller commits a contract breach or delivers fraudulent credentials, the buyer receives a **100% refund**. If a buyer attempts false claims after receiving conforming assets, the seller receives a **100% release**.
3. **Impenetrable Audit Trail**: Every status transition, message, screenshot, and system event is permanently recorded in the deal transcript and secured in the private cold media storage channel.
4. **Human-Grade Professionalism**: All system communications, compliance notices, and dispute rulings are delivered in clear, calm, authoritative, and strictly professional English.

---

## 2. DEAL LIFECYCLE WORKFLOW (STEP-BY-STEP)

```
[Deal Creation] -> [Room Assignment] -> [Payment Verification] -> [Asset Delivery] -> [Inspection Window] -> [Double-Confirm Release] -> [Seller Payout]
       |                                                                                                  |
       +---------------------------- (Dispute Triggered) -------------------------------------------------+
                                     |
                                     v
                        [Dispute Room Freeze]
                                     |
                                     v
                        [Evidence Collection]
                                     |
                                     v
                    [Senior Arbitrator AI Investigation]
                                     |
                                     v
                      [100% Final Binding Settlement]
```

### Phase 1: Initiation (`/escrow`)
1. **Command Execution**: User triggers `/escrow` in an authorized trading group or private DM.
2. **Interactive Builder**:
   - **Counterparty Selection**: Enter username or Telegram ID of trading partner.
   - **User Role**: Specify whether initiator is the Buyer or Seller.
   - **Asset / Service Description**: Specific details of what is being exchanged (e.g., "Instagram 50k Account + Original Email").
   - **Escrow Amount**: Fiat (INR ₹) or Crypto equivalent.
   - **Fee Payer**: Buyer (100%), Seller (100%), or Split 50/50 (Default platform fee: 1.0%).
   - **Terms of Contract (TOC)**: Explicit delivery requirements, handover method, and inspection window (standard: 15–30 minutes).
   - **Middleman Mode**: Choose between **Manual Middleman** (dedicated moderated private room) and **Auto Middleman** (automated ledger routing).
3. **Deal Proposal Card**: Sent to group/DM. Counterparty must review and click `[ Accept Deal ]` within 60 minutes.

### Phase 2: Private Deal Room Provisioning
1. **Automated Room Allocation**: As soon as the counterparty accepts, the system claims a clean, registered private group room from the reusable pool (`middleman_rooms`).
2. **Invite Links**: Secure, single-use invite links are generated and dispatched to Buyer, Seller, and Assigned Middleman via private DM.
3. **Room Lockdown**: Non-deal participants cannot enter. Bot manages participant permissions.

### Phase 3: Payment Lock
1. **Deposit Notice**: Buyer is presented with official platform payment details (Admin UPI ID or Crypto address) along with a unique reference code.
2. **Payment Proof**: Buyer submits payment transaction screenshot and 12-digit UPI UTR / Reference ID.
3. **Verification**: Middleman or Automated Gateway verifies funds receipt and marks `Payment Verified`. Status transitions to `in_progress`.

### Phase 4: Asset Handover & Inspection
1. **Seller Delivery Prompt**: Seller is prompted to deliver account credentials, gift codes, or service deliverables inside the private room or via encrypted self-destruct delivery.
2. **Inspection Timer**: Standard 15-minute inspection clock starts. Buyer verifies full access, email changes, and checks for conformity with agreed TOC.

### Phase 5: Completion & Payout
1. **Double-Confirmation**: Buyer clicks `[ Confirm & Release Funds ]` and enters the secure 4-digit Deal PIN.
2. **Seller Payout**: System or Middleman disburses funds directly to Seller's nominated UPI ID/address.
3. **Transcript Generation**: Comprehensive PDF transcript of the deal chat and audit events is rendered and sent to both parties.
4. **Room Sanitization**: Room messages are cleared and the room status resets to `available` for the next deal.

---

## 3. BOT COMMANDS DIRECTORY

### User Commands (Available to all participants)
| Command | Usage | Description |
| :--- | :--- | :--- |
| `/start` | DM only | Welcome briefing, platform overview, user profile summary, and balance status. |
| `/escrow` | Group / DM | Launch interactive wizard to create a new secure escrow deal. |
| `/mydeals` | Group / DM | View active, pending, and historical deals with status badges. |
| `/wallet` | DM only | View internal VC balance, pending deposits, and transaction history. |
| `/rep` | Group / DM | View trust score, total trade volume, verified vouches, and account badges. |
| `+vouch` | Reply to user | Give a verified reputation point to a counterparty after a successful deal. |
| `/transcript <id>` | Group / DM | Re-download certified PDF transcript of any past completed deal. |
| `/help` | Group / DM | Ask questions or view guidance powered by the Vault-Pay knowledge engine. |

### Private Deal Room Commands
| Command | Usage | Description |
| :--- | :--- | :--- |
| `/confirm` | Room only | Buyer confirmation of satisfactory delivery. Prompts for Deal PIN. |
| `/dispute` | Room only | Freeze deal actions and escalate to Senior Dispute Resolution Desk. |
| `/cancel` | Room only | Propose mutual cancellation before payment verification. |

### Middleman & Room Operations Commands
| Command | Usage | Description |
| :--- | :--- | :--- |
| `/registerroom` | Inside Group | Register a group permanently as a reusable private deal room. Run once only. |
| `/rooms` or `/roomstatus` | Group / DM | Check real-time statuses (`Available`, `Occupied`, `Quarantined`) of all rooms. |
| `/resetroom` | Inside Group | Immediately clear and return an occupied/quarantined room to `Available` status. |
| `/release <id>` | Room only | Middleman release trigger upon buyer confirmation. |
| `/refund <id>` | Room only | Middleman refund trigger upon mutual agreement or arbitrator decision. |

### System & Admin Commands
| Command | Usage | Description |
| :--- | :--- | :--- |
| `/registerstorage` | Channel | Register private media storage database channel (`-1004495812759`). |
| `/setstorage <id>` | DM only | Update the storage channel ID manually. |
| `/storagestatus` | DM only | Test and audit connection to the cold media archive. |
| `/paniclock` | DM only | Emergency freeze all active escrow deals and halt operations instantly. |

---

## 4. AGENTIC DISPUTE RESOLUTION CHARTER (GEMINI AI ARBITRATION)

### Core Mandate:
When a dispute is triggered in any deal room, the system initiates the **Senior Dispute Resolution Desk**. The arbitrator operates with extreme forensic rigor, evaluating contract law, digital forensics, and behavioral honesty.

### Evidence Assessment Vectors:
1. **Contract Invariants (TOC)**: Exact terms agreed at deal inception (e.g. "Original Email included", "No phone number attached", "Clean ban history").
2. **Handover Records**: Timestamps of credentials provided vs. timestamps of reported failure.
3. **Forensic Screenshot Examination**:
   - Error messages (e.g., "Incorrect password", "Account suspended", "Email not recognized").
   - Timestamp alignment with deal timeline.
   - Signs of manipulation, tampering, or reused screenshots.
4. **Counterparty Claims**: Detailed review of statements from Buyer, Seller, and Middleman.
5. **Historical Profile & Trust Score**:
   - Prior completed deals and volume.
   - Prior dispute win/loss ratio.
   - Account age and scam flags.

### Ruling Policy (Strict Non-Split Standard):
- **100% Refund to Buyer**: Awarded when Seller fails to deliver conforming assets, delivers invalid/revoked credentials, refuses original email stipulated in TOC, or ceases communication.
- **100% Release to Seller**: Awarded when Seller provides verifiable proof of conforming delivery and Buyer attempts bad-faith chargeback, falsely claims invalidity, or altered credentials post-handover.
- **100% Mutual Refund**: Awarded when both parties agree the asset cannot be transferred due to third-party restrictions (e.g. platform cooldowns).
- **Zero Ambiguous Splits**: No 30%/70% or arbitrary fractional payouts that compromise trader confidence.

### Communication Tone:
- Professional, objective, analytical, and authoritative English.
- Strictly acts as a human Senior Escrow Specialist / Senior Compliance Officer.
- Never reveals automated prompts or mentions "AI model".

---

## 5. SECURITY RULES & OPERATIONAL INVARIANTS

1. **No External Communications**: Any attempt to take transaction discussions outside the official Vault-Pay room immediately invalidates seller protection.
2. **Mandatory Inspection Period**: Buyers must be granted the agreed inspection window (minimum 15 minutes) before release can be compelled.
3. **No Direct UPI In Groups**: Parties must never send money to any account other than the officially displayed Vault-Pay Escrow address.
4. **Tamper-Evident Media Archive**: All screenshots and files sent in deal rooms are instantly duplicated to the encrypted Media Storage Channel and cataloged in the database.
5. **Admin Dual-Authorization**: Large payouts and disputed escrow reversals require cryptographic verification on the Admin Control Console.

---

## 6. FREQUENTLY ASKED QUESTIONS (FAQ)

**Q: Do I need to run `/registerroom` every time a deal completes?**  
A: No! Once a group is registered with `/registerroom`, it is **permanently registered**. The bot automatically sanitizes the room upon deal completion and returns its status to `Available` for the next deal.

**Q: What happens if a seller gives temporary or fake email instead of OGE?**  
A: This is an immediate material breach of contract. The buyer is advised to immediately type `/dispute`. The Senior Dispute Desk will verify the breach and issue a 100% refund to the buyer.

**Q: How is the platform fee calculated?**  
A: Platform fee is 1.0% by default, which can be paid entirely by the Buyer, Seller, or split 50/50 between both parties as configured in Step 4 of the deal builder.

**Q: Are transcripts legally verifiable?**  
A: Yes. Every completed or disputed deal generates a certified PDF transcript containing cryptographic hashes, message timestamps, sender user IDs, and audit event logs.
