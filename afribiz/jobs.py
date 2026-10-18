"""Scheduled jobs: the assistant reaching out first.

Run from cron (or any scheduler), e.g.:
    0 19 * * *  afribiz jobs evening     # evening summary + a timely lesson
    0 8 * * 1   afribiz jobs debts       # Monday morning: who to chase this week

WhatsApp only allows free-form messages within 24 hours of the owner's last message. Owners who
have been quiet longer are skipped here (or reached with an approved template, if configured),
which also keeps the assistant from becoming noise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from . import actions, learning
from .channels import Sender
from .config import Settings
from .db import Database
from .nlu.intents import Intent
from .replies import Reply
from .services import businesses, customers, reports
from .timeutil import iso, utcnow

log = logging.getLogger(__name__)


@dataclass
class JobResult:
    sent: int = 0
    skipped_window: int = 0
    skipped_quiet: int = 0


def _in_window(conn, business_id: int, hours: int = 24) -> bool:
    since = iso(utcnow() - timedelta(hours=hours))
    return conn.execute(
        "SELECT 1 FROM messages WHERE business_id = ? AND direction = 'in' AND created_at >= ? LIMIT 1",
        (business_id, since),
    ).fetchone() is not None


def evening_summary(db: Database, settings: Settings, sender: Sender) -> JobResult:
    result = JobResult()
    with db.tx() as conn:
        for biz in businesses.all_active(conn):
            if not biz.daily_summary:
                continue
            summary = reports.summary(conn, biz, "today")
            if not summary.has_activity:
                result.skipped_quiet += 1
                continue
            if sender.name == "whatsapp" and not _in_window(conn, biz.id):
                result.skipped_window += 1
                continue
            ctx = actions.Context(conn, biz, settings, channel="job")
            replies: list[Reply] = actions.report(ctx, Intent("report", {"period": "today"}))
            for signal in learning.signals(conn, biz):
                lesson = learning.lesson_for_trigger(conn, biz, settings.content_dir, signal)
                if lesson:
                    replies.append(actions.send_lesson(ctx, lesson))
                    break
            sender.send(biz.phone, replies)
            result.sent += 1
    return result


def debt_digest(db: Database, settings: Settings, sender: Sender) -> JobResult:
    result = JobResult()
    with db.tx() as conn:
        for biz in businesses.all_active(conn):
            debtors = customers.debtors(conn, biz)
            if not debtors:
                result.skipped_quiet += 1
                continue
            if sender.name == "whatsapp" and not _in_window(conn, biz.id):
                if settings.wa_reminder_template and hasattr(sender, "send_template"):
                    total = sum(d.balance for d in debtors)
                    ctx = actions.Context(conn, biz, settings)
                    sender.send_template(biz.phone, settings.wa_reminder_template, "en",
                                         [str(len(debtors)), ctx.money(total)])
                    result.sent += 1
                else:
                    result.skipped_window += 1
                continue
            ctx = actions.Context(conn, biz, settings, channel="job")
            replies = actions.debtors(ctx, Intent("debtors"))
            replies.append(Reply("Send *remind* and I'll prepare a polite message for each of them."))
            sender.send(biz.phone, replies)
            result.sent += 1
    return result
