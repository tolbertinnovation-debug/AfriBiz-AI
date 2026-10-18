"""Command line: run the server, chat in the terminal, import statements, run jobs, seed a demo."""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="afribiz", description="AfriBiz AI business assistant")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the web server (webhooks, simulator, dashboards)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    chat = sub.add_parser("chat", help="chat with the assistant in the terminal")
    chat.add_argument("--phone", default="254712345678", help="owner number, country code first")

    imp = sub.add_parser("import-statement", help="import a mobile money / bank statement CSV")
    imp.add_argument("--phone", required=True)
    imp.add_argument("--file", required=True)
    imp.add_argument("--provider", default="statement")

    jobs = sub.add_parser("jobs", help="run a scheduled job now")
    jobs.add_argument("name", choices=["evening", "debts"])

    demo = sub.add_parser("demo", help="create a demo business with four months of history")
    demo.add_argument("--phone", default="254700000001")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("afribiz.web.app:main_app", factory=True, host=args.host, port=args.port, reload=args.reload)
        return 0

    from .config import get_settings
    from .db import get_db

    settings = get_settings()
    db = get_db()

    if args.cmd == "chat":
        from .assistant import Assistant

        assistant = Assistant(db=db, settings=settings)
        print("AfriBiz chat. Type 'quit' to exit.", "(AI on)" if assistant.agent else "(rules only)")
        while True:
            try:
                text = input("you> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if text.strip().lower() in {"quit", "exit"}:
                return 0
            for reply in assistant.handle(args.phone, text, channel="cli"):
                print(f"\nafribiz> {reply.text}")
                if reply.buttons:
                    print("  [" + " | ".join(b.id for b in reply.buttons) + "]")
                if reply.document:
                    print(f"  📄 {reply.document.path}")
            print()

    if args.cmd == "import-statement":
        from .services import businesses, momo

        with open(args.file, encoding="utf-8-sig") as fh:
            data = fh.read()
        with db.tx() as conn:
            biz, _ = businesses.get_or_create(conn, args.phone)
            result = momo.import_csv(conn, biz, data, provider=args.provider)
        print(f"added {len(result.added)}, duplicates {result.duplicates}, unreadable rows {result.unparsed}")
        return 0

    if args.cmd == "jobs":
        from . import jobs as jobs_mod
        from .channels import get_sender

        job = {"evening": jobs_mod.evening_summary, "debts": jobs_mod.debt_digest}[args.name]
        print(job(db, settings, get_sender(settings)))
        return 0

    if args.cmd == "demo":
        from .actions import Context, link
        from .demo import seed
        from .services import businesses

        business_id = seed(db, args.phone)
        with db.tx() as conn:
            ctx = Context(conn, businesses.get(conn, business_id), settings)
            print(f"Demo business created for {args.phone}.\nDashboard: {link(ctx, 'dashboard', business_id)}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
