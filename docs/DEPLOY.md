# Deploying AfriBiz

## 1. Run the server

```bash
cp .env.example .env            # fill in at least AFRIBIZ_SECRET and AFRIBIZ_PUBLIC_URL
docker build -t afribiz .
docker run -d --env-file .env -p 8000:8000 -v afribiz-data:/data afribiz
```

Or without Docker: `pip install ".[all]" && afribiz serve`. Put it behind HTTPS (Caddy, nginx, or a PaaS
such as Render, Railway or Fly.io). The webhooks need a public HTTPS URL.

## 2. Connect WhatsApp (Meta Cloud API)

1. Create a Meta developer app with the **WhatsApp** product, and add a phone number in WhatsApp Manager.
2. Copy the **phone number ID** and a **permanent access token** (system user) into `WHATSAPP_PHONE_NUMBER_ID`
   and `WHATSAPP_TOKEN`. Copy the app secret into `WHATSAPP_APP_SECRET` (used to verify `X-Hub-Signature-256`).
3. Webhook URL: `https://your-domain/webhooks/whatsapp`, verify token = `WHATSAPP_VERIFY_TOKEN`.
   Subscribe to the `messages` field.
4. Optional: create and approve a utility template for the weekly debt digest (body with two parameters: number
   of debtors and total owed) and set `WHATSAPP_REMINDER_TEMPLATE`.

**Prototyping instead with the Twilio sandbox:** set `TWILIO_*` and point the sandbox's "when a message comes
in" URL to `https://your-domain/webhooks/twilio`.

## 3. Feature phones: USSD and SMS (Africa's Talking)

- Create a USSD channel and set its callback to `https://your-domain/webhooks/ussd`.
- For two-way SMS, set the inbound callback to `https://your-domain/webhooks/sms` and fill `AT_USERNAME`,
  `AT_API_KEY` (and `AT_SENDER_ID` if you have one).

## 4. Turn on the AI layer (optional)

Set `ANTHROPIC_API_KEY`. Without it, the rule engine still handles the common phrasings.
`AFRIBIZ_LLM_MODEL` defaults to `claude-opus-5-5`; `claude-haiku-5-5` is far cheaper per message.
Check quality on real messages before you choose.

## 5. Voice notes (optional)

Point `AFRIBIZ_STT_URL` at any OpenAI-compatible `/v1/audio/transcriptions` endpoint: a self-hosted
whisper.cpp or faster-whisper server, or a hosted provider. For better accuracy in local languages, compare
providers that specialise in African languages on your own users' voice notes.

## 6. Scheduled jobs

```cron
50 18 * * *  afribiz jobs evening   # summary + a lesson chosen from the owner's own numbers
50 7 * * 1   afribiz jobs debts     # Monday: who owes you, with a nudge to send reminders
```

On platforms without cron, call `POST /admin/jobs/evening` with header `X-Admin-Secret: $AFRIBIZ_SECRET`.

## 7. Production checklist

- [ ] `AFRIBIZ_SECRET` is long and random. `AFRIBIZ_ENABLE_SIMULATOR=false`
- [ ] `WHATSAPP_APP_SECRET` set (signature verification is skipped when it is empty)
- [ ] Daily backups of the `/data` volume (SQLite file + invoices)
- [ ] Privacy notice covering consent for credit sharing and the `delete my data` command
      (Kenya DPA 2019, Nigeria NDPA 2023, Ghana DPA 2012, Uganda DPPA 2019, South Africa POPIA)
- [ ] Before you partner with lenders: calibrate the credit score on real repayment outcomes
