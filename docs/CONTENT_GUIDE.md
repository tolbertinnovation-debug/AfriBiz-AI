# Content guide: turning teaching material into AfriBiz lessons

Lessons live in `content/lessons/`, one Markdown file per module. Trainers can edit them without touching
code; restart the server to load changes.

## File format

```markdown
# Module: Managing stock
order: 5

## Never run out of your best sellers
id: stock-01
triggers: low_stock
topics: stock, reorder, inventory

Two or three short paragraphs. Use *bold* for key words (WhatsApp formatting).
Write for a phone screen: short sentences, local examples, real numbers.

**Try this:** One concrete action the owner can take today.

**Quiz:** One question that checks the key idea?
- A) Wrong option
- B) Right option *
- C) Wrong option
**Why:** One sentence explaining the right answer.
```

| Field | Meaning |
|---|---|
| `order` (module) | Position of the module in the curriculum |
| `id` | Stable, unique id. **Translations reuse the same id** |
| `triggers` | Business situations that make the lesson timely (see below) |
| `topics` | Words owners might ask for ("lesson pricing") |
| `*` after an option | Marks the correct answer |

## Triggers

The assistant checks the owner's numbers and sends the first unseen lesson matching the most urgent signal,
both when they ask for a lesson and in the evening summary:

| Trigger | Fires when |
|---|---|
| `losing_money` | Month's costs + expenses exceed sales |
| `owner_draws` | Personal withdrawals exceed 30% of gross profit |
| `many_debtors` | Money owed by customers exceeds 25% of monthly sales |
| `low_stock` | Any tracked item at or below its reorder level |
| `low_margin` | Gross margin under 15% |
| `no_records` | Nothing recorded this month |
| `onboarding` | Used for first lessons |
| `credit_low` | Reserved for credit-profile nudges |

## Translations

Create `NN-module.<lang>.md` (e.g. `01-money-basics.sw.md`) with `language: sw` in the module header and the
**same lesson ids**. Untranslated lessons fall back to English, so the curriculum stays complete. Supported
UI languages: `en`, `sw`, `fr`, `pcm` (Nigerian Pidgin); add more in `afribiz/i18n.py`.

## Writing tips (from the shipped lessons)

- One idea per lesson, under ~120 words of body text.
- Use the owner's world: kiosks, market stalls, chama/susu, M-PESA, MoMo, boda/okada, school-fee months.
- Show the sum: "330 ÷ 28 = 11.8 per egg" sticks better than "include all costs".
- Point to the assistant feature that applies the lesson ("send *report week*", "tell me *count sugar 12*").
- Keep quiz answers spread across A/B/C. `tests/test_learning.py` validates all content.
