# Architecture

```
 WhatsApp Cloud API ─┐
 Twilio (WA / SMS) ──┤                 ┌── onboarding & pending follow-ups
 Africa's Talking ───┼─► channels ─►  Assistant ──┼── mobile-money SMS parser ──► momo history ─► credit profile
   (USSD, SMS)       │   (webhooks)    │           ├── rule engine (free, offline) ─┐
 Web simulator / CLI ┘                 │           └── Claude + tools (optional) ───┤
                                       │                                            ▼
 voice notes ─► speech-to-text ────────┘                         actions (validated) ─► services ─► SQLite
                                                                        │
                                                learning engine ◄───────┘  (lessons triggered by the numbers)
```

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Channels | `afribiz/channels/` | Provider payloads ⇄ `Inbound` / `Reply`. Signature checks, media download, 24h window |
| Assistant | `afribiz/assistant.py` | One entry point. Dedupes webhook retries, onboarding, pending questions, routing |
| Understanding | `afribiz/nlu/rules.py`, `afribiz/nlu/llm.py` | Text → `Intent`. Rules first; Claude for the long tail |
| Actions | `afribiz/actions.py` | Executes intents, phrases confirmations. Shared by rules, AI, USSD |
| Services | `afribiz/services/` | Bookkeeping engine: ledger, stock, customers, invoices, reminders, reports, credit |
| Mobile money | `afribiz/mobilemoney/`, `services/momo.py` | SMS + CSV statement parsing, dedupe by reference |
| Learning | `afribiz/learning/`, `content/lessons/` | Markdown lessons, triggers, quizzes, progress |
| Web | `afribiz/web/` | FastAPI app, simulator, owner dashboard, lender view, signed file links |
| Jobs | `afribiz/jobs.py` | Evening summary + lesson, weekly debt digest |

## Key decisions

**Rules before AI.** Most messages are simple ("sold 3 bread @ 60"). The rule engine handles them instantly,
at no cost and offline, in English, Swahili, French and Pidgin. Only messages the rules can't parse go to Claude,
which keeps per-user cost low enough for informal-sector price points.

**The AI never writes to the database directly.** Claude calls strict-schema tools that map onto the same
validated actions the rules use. Every tool runs inside a SQLite savepoint, so a failing call leaves no partial
records behind. The books stay deterministic and auditable, and an "undo" works the same way whichever path
recorded the entry.

**Model settings.** The default is `claude-opus-5-5` with adaptive thinking at `low` effort (short chat turns) and
`fallbacks: "default"` for refusal recovery. Set `AFRIBIZ_LLM_MODEL=claude-haiku-5-5` for a much cheaper
deployment, and measure quality on your own traffic before switching. Inside one message the tool loop is
append-only (passing `response.content` back unchanged). Across messages only plain text of recent turns is
replayed, so history can be trimmed without editing anything the model reasoned over.

**Cash-basis accounting that teaches.** Kinds: `sale` (with `paid_amount`; the rest is receivable),
`payment_in`, `purchase` (stock at cost, *not* an expense), `expense`, `owner_draw` (personal money, not an
expense) and `capital_in`. Reports show both **profit** (sales − cost of goods − expenses) and **cash**
(in/out). The difference between those two is the most important lesson for a small business.

**Money as integers.** All amounts are minor units. Display respects currencies that are quoted without
decimals (KES, UGX, TZS, XOF…). Currency and timezone are inferred from the owner's dialling code.

**Explainable credit.** Eight factors (history, regular income, stability, surplus, payer diversity, buffer,
loan repayment, record keeping) worth 100 points in total. Thin files are capped at 40 and graded E. The
affordable loan is ~35% of monthly surplus (capped at 15% of business income) × a grade-based tenor.
Sharing requires recorded, revocable consent.

**Privacy.** Signed, unguessable links for dashboards and files. `delete my data` erases everything (the
right to erasure under Kenya's DPA 2019 and Nigeria's NDPA 2023). WhatsApp webhook signatures are verified,
Twilio signatures likewise, and inbound message IDs are de-duplicated.

## Data model (SQLite)

`businesses` · `items` · `customers` · `transactions` (+ `transaction_lines`, `stock_movements`) · `invoices` ·
`momo_transactions` · `messages` · `lesson_progress` · `reminders` · `consents` · `processed_inbound`.

SQLite (WAL mode) comfortably serves thousands of small businesses on one small VM. The services layer only
uses plain SQL, so moving to Postgres later is a contained change.
