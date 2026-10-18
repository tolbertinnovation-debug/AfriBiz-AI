# AfriBiz AI

**A business assistant for small and informal businesses in Africa, used through WhatsApp, voice notes and USSD.**

Most African businesses are small and informal, and they already run on two tools: WhatsApp and mobile money.
AfriBiz works inside those tools, so there is no app to install and no forms to fill. An owner texts or sends
a voice note in their own words, and the assistant:

- 🧾 **keeps the books**: sales (cash and credit), expenses, stock bought, owner's drawings, profit *and* cash
- 📦 **tracks stock**: moving-average cost, margins, reorder alerts, stock counts that expose shrinkage
- 📄 **drafts invoices**: WhatsApp text plus a PDF to forward
- 📨 **chases customers**: polite, escalating reminders with one-tap `wa.me` links sent from the owner's own number
- 📈 **builds a credit profile** from forwarded M-PESA / MoMo / Airtel / bank-alert SMS or statement CSVs.
  The profile is explainable, the owner controls it, and they can share it with a lender by consent
- 🎓 **teaches**: one-minute Small Business Management lessons with quizzes, sent when the owner's own numbers
  make them relevant (e.g. credit-control lessons when debts pile up)

```
you> nimeuza mkate 3 @ 60
✅ Sale recorded: KSh 180
• 3 × Mkate @ KSh 60 = KSh 180
📈 Profit on this sale: KSh 30

you> sold 2 sugar to Mary on credit
💬 How much is one Sugar? Reply with the price, e.g. 120
you> 120
✅ Sale recorded: KSh 240 … 📒 Mary owes KSh 240

you> [forwards] QK12AB34CD Confirmed. You have received Ksh240.00 from MARY WANJIKU …
📩 M-PESA: KSh 240 received from MARY WANJIKU
What was this money for?  [1 A sale] [2 Debt payment] [3 Not business]
```

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

afribiz demo            # a Nairobi kiosk with 4 months of sales, stock and M-PESA history
afribiz serve           # http://localhost:8000/simulator  (WhatsApp-style chat in the browser)
afribiz chat            # or chat in the terminal
pytest                  # 121 tests
```

The demo prints a private dashboard link. Open it to see the owner dashboard: KPIs, a 30-day chart,
debtors with reminder links, stock, credit factors and a statement upload.

Set `ANTHROPIC_API_KEY` to enable the AI layer (Claude with tools) for free-form and mixed-language messages
and business questions. Without a key, the built-in rule engine handles the common phrasings in English,
Swahili, French and Pidgin.

## What owners can say

| Goal | Examples |
|---|---|
| Sales | `sold 3 bread @ 60` · `sold 2 rice @150, 3 soap at 50 to Mama Ngozi paid 400` · `nimeuza mkate 3 @ 60` · `vendu 3 pains à 60` · `I don sell 4 bread for 400 to Emeka for credit` |
| Expenses | `spent 200 transport` · `rent 5000` · `nimetumia 200 nauli` · `took 500 for home` *(owner's drawing)* |
| Stock | `bought 20 soda for 800 sell at 60` · `stock` · `count sugar 12` · `reorder rice at 5` · `price rice 160` |
| Customers | `Mary paid 300` · `Juma amelipa 200` · `debtors` · `remind` · `remind Juma` · `phone Juma 0712345678` |
| Invoices | `invoice Juma 2 rice @ 150 due 14 days` |
| Reports | `report` · `report week` · `report month` · `recent` · `dashboard` |
| Mobile money | forward any M-PESA / MoMo / Airtel / bank-alert SMS (several at once is fine) · `score` · `share score` · `stop sharing` |
| Learning | `lesson` · `lesson pricing` · `lesson 5` · `progress` · reply `A`/`B`/`C` to quizzes |
| Settings & rights | `undo` · `language sw` · `currency NGN` · `summary off` · `delete my data` |

Feature-phone users dial a USSD code for: today's summary, record a sale or expense, who owes me, low stock,
credit score, business tip.

## Channels

| Channel | Endpoint | Notes |
|---|---|---|
| WhatsApp Cloud API | `/webhooks/whatsapp` | Signature-verified, retries de-duplicated, voice notes, buttons, PDF documents, CSV statements |
| Twilio WhatsApp/SMS | `/webhooks/twilio` | TwiML replies; good for sandbox prototyping |
| Africa's Talking USSD | `/webhooks/ussd` | `CON`/`END` menus for basic phones |
| Africa's Talking SMS | `/webhooks/sms` | Two-way SMS |
| Browser simulator | `/simulator` | For demos and user testing (disable in production) |

## Project layout

```
afribiz/
  assistant.py        one entry point for every channel
  actions.py          validated execution + chat-friendly confirmations
  nlu/rules.py        multilingual rule engine (free, offline)
  nlu/llm.py          Claude tool-use layer (optional)
  services/           ledger, inventory, customers, invoices, reminders, reports, credit, momo
  mobilemoney/        SMS parser for M-PESA, MoMo, Airtel, Telecel, Orange, Wave, Nigerian bank alerts
  learning/           lesson engine (triggers, quizzes, progress)
  channels/           WhatsApp Cloud, Twilio, Africa's Talking
  web/                FastAPI app, dashboard, lender view, simulator
  jobs.py             evening summary, weekly debt digest
content/lessons/      the curriculum: Markdown, editable by trainers
docs/                 RESEARCH, ARCHITECTURE, CONTENT_GUIDE, DEPLOY
```

## Your teaching material as the content layer

The curriculum ships with 25 lessons in 11 modules, from separating business and home money through
costing, cash flow, stock, credit control, marketing, savings, credit building and mobile-money fraud to
formalisation. One module is also translated into Swahili. Each lesson is plain Markdown with a title,
a short body, one action, a quiz and *triggers*. To plug in your own Small Business Management course, convert
each unit into this format; see [docs/CONTENT_GUIDE.md](docs/CONTENT_GUIDE.md).

## Further reading

- [docs/RESEARCH.md](docs/RESEARCH.md): market, competitors, credit-scoring evidence, SMS formats, channel constraints (with sources)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): design decisions, accounting model, credit model, privacy
- [docs/DEPLOY.md](docs/DEPLOY.md): Docker, WhatsApp/Twilio/Africa's Talking setup, AI and voice, cron, production checklist

## Important limits

- The credit profile is a **pre-qualification indicator**, not a lending decision. Forwarded SMS can be
  forged; lenders should verify with provider statements and calibrate the score on their own repayment data.
- SMS formats change. The parser is template-free and tested on common formats, but add real samples from new
  markets to `tests/test_mobilemoney.py` as you expand.
- WhatsApp only allows business-initiated free-form messages within 24 hours of the owner's last message.
  The jobs respect that window.
