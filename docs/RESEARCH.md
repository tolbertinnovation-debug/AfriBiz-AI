# Research notes: why this product, and the design choices behind it

## 1. The opportunity

- **Most African work is informal.** The UN Economic Commission for Africa puts informal employment at about
  **83% of all jobs in 2024**, barely down from 84.3% in 2005. ILO figures cited for 2023 put it at **86.5%**,
  against 58% worldwide. The informal economy is estimated at **30–40% of GDP**.
  ([North Africa Post / UN data](https://northafricapost.com/96624-africas-informal-economy-employs-83-of-workforce-in-2024-un-data.html),
  [The EastAfrican](https://www.theeastafrican.co.ke/tea/business-tech/africa-creates-15m-jobs-but-working-poor-grow-4490632))
- The ILO notes that *"the vast majority of the labour force and micro- and small enterprises operate in the
  informal economy"*. ([ILO infographic](https://www.tralac.org/images/docs/8651/transition-from-the-informal-to-the-formal-economy-in-africa-ilo-infographic-november-2015.pdf))
- These businesses already run on two tools: **WhatsApp** and **mobile money**. A product that lives inside
  both, rather than asking owners to install and learn a new app, removes the biggest adoption barrier.

## 2. What exists today (and the gap)

| Player | What it does | Gap we target |
|---|---|---|
| Kippa (NG) | Mobile bookkeeping app: sales, expenses, invoices, inventory, debt recovery; 350k+ SMEs (2022); later payments | Separate app to install and learn; form-based input |
| Sabi (NG) | B2B commerce and digital tools for small retailers | Commerce first; records secondary |
| M-Ledger, PesaApps and others | App-based ledgers | Same app-install friction; English-first forms |

Sources: [startuplist.africa (Kippa)](https://www.startuplist.africa/startups/kippa),
[Financial IT](https://financialit.net/news/payments/kippa-launches-new-digital-payments-product-smes-africa),
[startuplist.africa bookkeeping](https://startuplist.africa/industry/bookkeeping).

**AfriBiz's angle:** no app at all. You *talk* to it on WhatsApp (text or voice, in your own mix of languages),
or use USSD on a feature phone. It takes in the mobile-money SMS owners already receive, and builds a credit
profile the owner controls. It also teaches business skills in the moment they matter.

## 3. Credit from cash-flow data

- Mobile money transaction data is one of the strongest alternative-data signals in Africa. Lenders such as
  FairMoney (transaction velocity, digital footprint, with consent) and Kifiya in Ethiopia (mobile transactions,
  utilities, e-commerce) lend to MSMEs without collateral on this basis.
  ([itweb](https://itweb.africa/content/LPwQ5Ml6NabvNgkj), [IDB Invest](https://www.idbinvest.org/en/blog/financial-institutions/five-fintechs-disrupting-msme-finance),
  [MSME Africa](https://msmeafricaonline.com/fairmoney-mfb-leverages-alternative-credit-scoring-to-expand-sme-access-to-%E2%82%A65m-collateral-free-loans-in-nigeria/))
- Merchant lenders such as Yoco underwrite on daily revenue, peak periods, consistency of sales and average
  transaction size. Our factors mirror these: activity, stability, surplus, diversity, buffer and repayment history.
- **Caveats we designed for:**
  1. Cash still dominates many markets, so the self-reported ledger contributes a "good records" factor.
  2. Forwarded SMS can be faked, so the lender view says it is a *pre-qualification indicator*, and the
     assistant runs a balance-consistency check on each forwarded payment.
  3. Consent is required. Sharing is opt-in, revocable and logged.
  ([Krutham](https://www.krutham.com/insights/data-as-collateral-banks-need-to-recalibrate-lending-models/),
  [arXiv: interpretable hybrid scoring for thin-file borrowers](https://arxiv.org/pdf/2608.26837))
- Explainability is a product feature: every point in the score maps to a factor and a concrete tip, so
  owners learn how to become creditworthy rather than receiving an opaque number.

## 4. Mobile money message formats

- **M-PESA (Safaricom):** every confirmation starts with a 10-character code followed by "Confirmed". There
  are distinct templates for Send Money, Pay Bill (business plus account), Buy Goods, Money Received and
  Withdrawal. A single message carries several amounts (the amount, the fee and the balance), so naive "first
  number after Ksh" parsing picks the wrong one.
  ([imagetotable.ai](https://imagetotable.ai/blog/mpesa-screenshot-transaction))
  Safaricom is adding "Jumbe" split notifications for businesses, which prove a payment without revealing the
  balance ([Techweez, Oct 2026](https://techweez.com/2026/10/06/safaricom-jumbe-mpesa-split-notifications/)),
  so the parser does not require a balance.
- **MTN MoMo / Airtel Money:** the wording varies by country (e.g. Uganda's "You have received UGX … from …
  Bal", Ghana's "Payment received for GHS … Current Balance … Transaction ID"). There is no official public
  template. ([momoparse](https://pypi.org/project/momoparse/))
- **Design consequence:** the parser does not use one fixed template per provider. It finds the parts every
  confirmation has (direction verb, amount, counterparty, reference, balance, fee) and labels each figure by
  the words before it. Statement CSVs are imported by matching column names.

## 5. Channels

- **WhatsApp Cloud API:** inbound voice notes arrive as `type: audio` with a media ID that resolves to a
  short-lived URL (download with the bearer token). Voice notes are OGG/Opus.
  ([Meta audio docs](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/audio-messages))
  Interactive reply buttons allow up to 3 per message. Free-form and interactive messages are allowed only
  within **24 hours** of the user's last message; outside the window you need approved templates.
  ([360dialog](https://docs.360dialog.com/partner/messaging/message-types/interactive-messages),
  [Meta blog](https://developers.facebook.com/blog/post/2022/11/21/sending-interactive-messages/))
  → Scheduled summaries only go to owners inside the window. Debt reminders are sent as **click-to-chat
  (wa.me) links from the owner's own number**, which needs no templates and keeps the familiar relationship.
- **USSD (Africa's Talking):** form POST with `sessionId`, `serviceCode`, `phoneNumber` and `text` (all inputs
  so far joined by `*`, empty at the start). Reply with `CON …` to continue or `END …` to finish.
  ([guide](https://helloduty.com/blogs/getting-started-with-the-africas-talking-ussd-api))

## 6. Content layer

The lesson engine is built so that existing **Small Business Management teaching material** becomes the
content: plain Markdown modules (record keeping, costing and pricing, cash flow, stock, credit control,
marketing, savings and borrowing, credit building, mobile-money safety, formalisation). Each lesson is short
enough to read on a phone in about a minute, ends with one action and a one-question quiz, and has *triggers*
so it is delivered when the owner's own numbers make it relevant. For example, the credit-control lessons
arrive when receivables pass 25% of monthly sales. See `docs/CONTENT_GUIDE.md`.
