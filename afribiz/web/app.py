"""HTTP surface: channel webhooks, the browser simulator, owner dashboard and lender view."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__, actions
from ..assistant import Assistant, Inbound
from ..channels import africastalking, get_sender, whatsapp_cloud
from ..channels import twilio as twilio_channel
from ..config import Settings, get_settings
from ..db import Database, get_db
from ..replies import Reply
from ..security import sign, verify, verify_meta_signature, verify_twilio_signature
from ..services import businesses, credit, momo
from . import pages

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def create_app(db: Database | None = None, settings: Settings | None = None,
               assistant: Assistant | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = db or get_db()
    assistant = assistant or Assistant(db=db, settings=settings)
    app = FastAPI(title="AfriBiz AI", version=__version__)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.state.assistant = assistant
    app.state.settings = settings
    app.state.db = db

    def reply_json(replies: list[Reply]) -> list[dict[str, Any]]:
        out = []
        for r in replies:
            item: dict[str, Any] = {"text": r.text, "buttons": [{"id": b.id, "title": b.title} for b in r.buttons]}
            if r.document:
                item["document"] = {"filename": r.document.filename,
                                    "url": r.document.url or _file_url(settings, r.document.path)}
            out.append(item)
        return out

    # -- basics -----------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return pages.home(settings)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "ai": assistant.agent is not None,
                "whatsapp": settings.whatsapp_configured, "voice": bool(settings.stt_url)}

    # -- simulator --------------------------------------------------------------------------

    @app.get("/simulator", response_class=HTMLResponse)
    def simulator() -> str:
        if not settings.enable_simulator:
            raise HTTPException(404)
        return (STATIC / "simulator.html").read_text(encoding="utf-8")

    @app.post("/api/simulate")
    async def simulate(request: Request) -> JSONResponse:
        if not settings.enable_simulator:
            raise HTTPException(404)
        body = await request.json()
        phone = str(body.get("phone") or "").strip()
        text = str(body.get("text") or "")
        if len(businesses.normalize_phone(phone)) < 9:
            raise HTTPException(400, "phone must be an international number, e.g. 254712345678")
        replies = assistant.handle(phone, text, channel="simulator")
        return JSONResponse({"replies": reply_json(replies)})

    # -- WhatsApp Cloud API -----------------------------------------------------------------

    @app.get("/webhooks/whatsapp")
    def wa_verify(request: Request) -> Response:
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and settings.wa_verify_token and q.get("hub.verify_token") == settings.wa_verify_token:
            return PlainTextResponse(q.get("hub.challenge", ""))
        raise HTTPException(403)

    @app.post("/webhooks/whatsapp")
    async def wa_inbound(request: Request, background: BackgroundTasks) -> dict[str, str]:
        raw = await request.body()
        if not verify_meta_signature(settings.wa_app_secret, raw, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(401, "bad signature")
        payload = await request.json()
        for parsed in whatsapp_cloud.parse_webhook(payload):
            background.add_task(_process_whatsapp, assistant, settings, parsed)
        return {"status": "ok"}

    # -- Twilio -----------------------------------------------------------------------------

    @app.post("/webhooks/twilio")
    async def twilio_inbound(request: Request) -> Response:
        form = {k: str(v) for k, v in (await request.form()).items()}
        url = settings.public_base_url.rstrip("/") + "/webhooks/twilio"
        if not verify_twilio_signature(settings.twilio_auth_token, url, form, request.headers.get("X-Twilio-Signature")):
            raise HTTPException(401, "bad signature")
        inbound = twilio_channel.parse_form(form)
        media = twilio_channel.media_of(form)
        if media and media[1].startswith("audio"):
            inbound.audio = twilio_channel.download(settings, media[0])
            inbound.audio_mime = media[1]
        replies = assistant.handle_inbound(inbound)
        for r in replies:
            if r.document and not r.document.url:
                r.document.url = _file_url(settings, r.document.path)
        return Response(twilio_channel.twiml(replies), media_type="application/xml")

    # -- Africa's Talking -------------------------------------------------------------------

    @app.post("/webhooks/ussd", response_class=PlainTextResponse)
    def ussd(phoneNumber: str = Form(...), text: str = Form(""), sessionId: str = Form(""),  # noqa: N803
             serviceCode: str = Form("")) -> str:  # noqa: N803
        return africastalking.handle_ussd(assistant, phoneNumber, text)

    @app.post("/webhooks/sms")
    def sms(background: BackgroundTasks, from_: str = Form(..., alias="from"), text: str = Form("")) -> dict[str, str]:
        def work() -> None:
            replies = assistant.handle(from_, text, channel="sms")
            body = "\n\n".join(r.text for r in replies if r.text)
            for chunk in [body[i:i + 459] for i in range(0, len(body), 459)][:3]:  # max 3 concatenated SMS
                africastalking.send_sms(settings, from_, chunk)
        background.add_task(work)
        return {"status": "ok"}

    # -- owner dashboard --------------------------------------------------------------------

    def _owner(business_id: int, token: str):
        if not verify(settings.secret_key, "dashboard", business_id, token):
            raise HTTPException(404)
        with db.tx() as conn:
            try:
                return businesses.get(conn, business_id)
            except LookupError:
                raise HTTPException(404) from None

    @app.get("/d/{business_id}/{token}", response_class=HTMLResponse)
    def dashboard(business_id: int, token: str, imported: str | None = None) -> str:
        biz = _owner(business_id, token)
        with db.tx() as conn:
            return pages.dashboard(conn, biz, settings, token, imported)

    @app.post("/d/{business_id}/{token}/import", response_class=HTMLResponse)
    async def dashboard_import(business_id: int, token: str, file: UploadFile | None = File(None),
                               sms_text: str = Form("")) -> Response:
        biz = _owner(business_id, token)
        added = 0
        with db.tx() as conn:
            if file is not None and file.filename:
                data = (await file.read()).decode("utf-8", errors="replace")
                try:
                    added += len(momo.import_csv(conn, biz, data).added)
                except ValueError as exc:
                    return HTMLResponse(pages.error_page(str(exc)), status_code=400)
            if sms_text.strip():
                added += len(momo.import_sms_blob(conn, biz, sms_text).added)
        return Response(status_code=303, headers={"Location": f"/d/{business_id}/{token}?imported={added}"})

    # -- lender view (owner consent required) -------------------------------------------------

    @app.get("/lender/{business_id}/{token}", response_class=HTMLResponse)
    def lender(business_id: int, token: str) -> str:
        if not verify(settings.secret_key, "lender", business_id, token):
            raise HTTPException(404)
        with db.tx() as conn:
            try:
                biz = businesses.get(conn, business_id)
            except LookupError:
                raise HTTPException(404) from None
            if not credit.has_consent(conn, biz):
                raise HTTPException(403, "The business owner has not shared this profile.")
            return pages.lender(conn, biz)

    # -- files ------------------------------------------------------------------------------

    @app.get("/files")
    def files(p: str, t: str) -> FileResponse:
        if not verify(settings.secret_key, "file", p, t, 32):
            raise HTTPException(404)
        path = Path(p).resolve()
        root = Path(settings.data_dir).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="application/pdf", filename=path.name)

    # -- jobs (for platforms without cron) --------------------------------------------------

    @app.post("/admin/jobs/{name}")
    def run_job(name: str, request: Request) -> dict[str, Any]:
        if request.headers.get("X-Admin-Secret") != settings.secret_key or settings.secret_key == "change-me":
            raise HTTPException(403)
        from .. import jobs

        sender = get_sender(settings)
        job = {"evening": jobs.evening_summary, "debts": jobs.debt_digest}.get(name)
        if job is None:
            raise HTTPException(404)
        result = job(db, settings, sender)
        return result.__dict__

    return app


def _file_url(settings: Settings, path: str) -> str:
    from urllib.parse import quote

    return f"{settings.public_base_url.rstrip('/')}/files?p={quote(path)}&t={sign(settings.secret_key, 'file', path, 32)}"


def _process_whatsapp(assistant: Assistant, settings: Settings, parsed: whatsapp_cloud.ParsedMessage) -> None:
    client = whatsapp_cloud.WhatsAppCloudClient(settings)
    inbound: Inbound = parsed.inbound
    try:
        if parsed.media_id and parsed.media_kind == "audio":
            media = client.download_media(parsed.media_id)
            if media:
                inbound.audio, inbound.audio_mime = media
        elif parsed.media_id and parsed.media_kind == "document" and (parsed.filename or "").lower().endswith(".csv"):
            media = client.download_media(parsed.media_id)
            if media:
                replies = _import_statement(assistant, inbound.phone, media[0].decode("utf-8", errors="replace"))
                client.send(inbound.phone, replies)
                return
        replies = assistant.handle_inbound(inbound)
        if replies:
            client.send(inbound.phone, replies)
    except Exception:
        log.exception("failed to process WhatsApp message %s", inbound.message_id)


def _import_statement(assistant: Assistant, phone: str, data: str) -> list[Reply]:
    with assistant.db.tx() as conn:
        biz, _ = businesses.get_or_create(conn, phone)
        try:
            result = momo.import_csv(conn, biz, data)
        except ValueError as exc:
            return [Reply(f"I couldn't read that statement: {exc}")]
        ctx = actions.Context(conn, biz, assistant.settings)
        ins = sum(t.amount for t in result.added if t.direction == "in")
        return [Reply(f"📑 Statement imported: {len(result.added)} transactions (money in {ctx.money(ins)}). "
                      f"{result.duplicates} already known. Send *score* to see your credit profile.")]


def main_app() -> FastAPI:  # uvicorn factory entry point: uvicorn afribiz.web.app:main_app --factory
    logging.basicConfig(level=logging.INFO)
    return create_app()
