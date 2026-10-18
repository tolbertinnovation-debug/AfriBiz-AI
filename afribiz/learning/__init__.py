"""The content layer: short business lessons delivered in the chat, at the moment they are useful.

Lessons are plain Markdown files in `content/lessons/` (one file per module) so that teachers
and trainers can write and edit them without touching code. See docs/CONTENT_GUIDE.md.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..services.businesses import Business


@dataclass
class Quiz:
    question: str
    options: list[tuple[str, str]]  # (letter, text)
    answer: str  # letter
    explanation: str = ""


@dataclass
class Lesson:
    id: str
    module: str
    module_order: int
    order: int
    title: str
    body: str
    action: str = ""
    triggers: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    language: str = "en"
    quiz: Quiz | None = None

    def as_message(self, number: int | None = None, total: int | None = None) -> str:
        head = f"📘 *{self.title}*"
        if number and total:
            head += f"  ({number}/{total})"
        parts = [head, f"_{self.module}_", "", self.body.strip()]
        if self.action:
            parts += ["", f"✅ *Try this:* {self.action.strip()}"]
        if self.quiz:
            parts += ["", f"❓ *Quick quiz:* {self.quiz.question}"]
            parts += [f"{letter}) {text}" for letter, text in self.quiz.options]
            parts += ["Reply with the letter, e.g. *B*"]
        return "\n".join(parts)


_META_RE = re.compile(r"^([a-z_]+):\s*(.*)$")
_OPTION_RE = re.compile(r"^-\s*([A-Da-d])\)\s*(.+?)(\s\*)?$")


def parse_module(text: str, source: str = "") -> list[Lesson]:
    lines = text.splitlines()
    module_name = Path(source).stem
    module_order = 99
    language = "en"
    i = 0
    while i < len(lines) and not lines[i].startswith("## "):
        line = lines[i].strip()
        if line.startswith("# "):
            module_name = re.sub(r"^#\s*(Module:\s*)?", "", line).strip()
        elif m := _META_RE.match(line):
            if m.group(1) == "order":
                module_order = int(m.group(2))
            elif m.group(1) == "language":
                language = m.group(2).strip()
        i += 1

    lessons: list[Lesson] = []
    blocks: list[list[str]] = []
    for line in lines[i:]:
        if line.startswith("## "):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)

    for position, block in enumerate(blocks, start=1):
        title = block[0][3:].strip()
        meta: dict[str, str] = {}
        j = 1
        while j < len(block) and (m := _META_RE.match(block[j].strip())):
            meta[m.group(1)] = m.group(2).strip()
            j += 1
        body_lines: list[str] = []
        action_lines: list[str] = []
        quiz_q = ""
        options: list[tuple[str, str]] = []
        answer = ""
        why_lines: list[str] = []
        mode = "body"
        for raw in block[j:]:
            line = raw.rstrip()
            if line.startswith("**Try this:**"):
                mode = "action"
                line = line[len("**Try this:**"):].strip()
            elif line.startswith("**Quiz:**"):
                mode = "quiz"
                quiz_q = line[len("**Quiz:**"):].strip()
                continue
            elif line.startswith("**Why:**"):
                mode = "why"
                line = line[len("**Why:**"):].strip()
            if mode == "body":
                body_lines.append(line)
            elif mode == "action":
                action_lines.append(line)
            elif mode == "quiz":
                if om := _OPTION_RE.match(line.strip()):
                    letter = om.group(1).upper()
                    options.append((letter, om.group(2).strip()))
                    if om.group(3):
                        answer = letter
                elif line.strip():
                    quiz_q += " " + line.strip()
            elif mode == "why":
                why_lines.append(line)
        quiz = None
        if quiz_q and options and answer:
            quiz = Quiz(quiz_q.strip(), options, answer, " ".join(w.strip() for w in why_lines if w.strip()))
        lessons.append(Lesson(
            id=meta.get("id") or f"{Path(source).stem}-{position:02d}",
            module=module_name,
            module_order=module_order,
            order=position,
            title=title,
            body="\n".join(body_lines).strip(),
            action=" ".join(a.strip() for a in action_lines if a.strip()),
            triggers=[t.strip() for t in meta.get("triggers", "").split(",") if t.strip()],
            topics=[t.strip().lower() for t in meta.get("topics", "").split(",") if t.strip()],
            language=meta.get("language", language),
            quiz=quiz,
        ))
    return lessons


@lru_cache(maxsize=4)
def load_lessons(content_dir: str) -> tuple[Lesson, ...]:
    folder = Path(content_dir) / "lessons"
    lessons: list[Lesson] = []
    for path in sorted(folder.glob("*.md")):
        lessons.extend(parse_module(path.read_text(encoding="utf-8"), str(path)))
    lessons.sort(key=lambda lesson: (lesson.module_order, lesson.order))
    return tuple(lessons)


def _for_language(lessons: tuple[Lesson, ...], language: str) -> list[Lesson]:
    """The English curriculum, with each lesson swapped for its translation where one exists."""
    translated = {lesson.id: lesson for lesson in lessons if lesson.language == language}
    return [translated.get(lesson.id, lesson) for lesson in lessons if lesson.language == "en"]


def sent_ids(conn: sqlite3.Connection, business: Business) -> set[str]:
    return {r["lesson_id"] for r in conn.execute(
        "SELECT lesson_id FROM lesson_progress WHERE business_id = ?", (business.id,))}


def next_lesson(conn: sqlite3.Connection, business: Business, content_dir: str) -> Lesson | None:
    lessons = _for_language(load_lessons(content_dir), business.language)
    done = sent_ids(conn, business)
    for lesson in lessons:
        if lesson.id not in done:
            return lesson
    return None


def find_lesson(content_dir: str, language: str, query: str) -> Lesson | None:
    """Look a lesson up by number ('3'), id ('stock-02') or topic word ('pricing')."""
    lessons = _for_language(load_lessons(content_dir), language)
    q = (query or "").strip().lower()
    if not q:
        return None
    if q.isdigit():
        index = int(q) - 1
        return lessons[index] if 0 <= index < len(lessons) else None
    for lesson in lessons:
        if lesson.id.lower() == q:
            return lesson
    for lesson in lessons:
        if q in lesson.topics:
            return lesson
    for lesson in lessons:
        if q in lesson.title.lower() or q in lesson.module.lower():
            return lesson
    return None


def lesson_for_trigger(conn: sqlite3.Connection, business: Business, content_dir: str, trigger: str) -> Lesson | None:
    done = sent_ids(conn, business)
    for lesson in _for_language(load_lessons(content_dir), business.language):
        if trigger in lesson.triggers and lesson.id not in done:
            return lesson
    return None


def lesson_position(content_dir: str, language: str, lesson: Lesson) -> tuple[int, int]:
    lessons = _for_language(load_lessons(content_dir), language)
    ids = [x.id for x in lessons]
    return (ids.index(lesson.id) + 1 if lesson.id in ids else 0, len(lessons))


def mark_sent(conn: sqlite3.Connection, business: Business, lesson: Lesson) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO lesson_progress (business_id, lesson_id) VALUES (?, ?)", (business.id, lesson.id)
    )


def get_lesson(content_dir: str, lesson_id: str) -> Lesson | None:
    for lesson in load_lessons(content_dir):
        if lesson.id == lesson_id:
            return lesson
    return None


def record_answer(conn: sqlite3.Connection, business: Business, lesson: Lesson, letter: str) -> bool:
    correct = bool(lesson.quiz and lesson.quiz.answer == letter.upper())
    conn.execute(
        "UPDATE lesson_progress SET quiz_answer = ?, correct = ? WHERE business_id = ? AND lesson_id = ?",
        (letter.upper(), int(correct), business.id, lesson.id),
    )
    return correct


def progress(conn: sqlite3.Connection, business: Business, content_dir: str) -> tuple[int, int, int]:
    """(lessons_seen, total_lessons, quizzes_correct)."""
    total = len(_for_language(load_lessons(content_dir), business.language))
    row = conn.execute(
        "SELECT COUNT(*) AS seen, COALESCE(SUM(correct), 0) AS right FROM lesson_progress WHERE business_id = ?",
        (business.id,),
    ).fetchone()
    return int(row["seen"]), total, int(row["right"])


def signals(conn: sqlite3.Connection, business: Business) -> list[str]:
    """Business situations that make a particular lesson timely, most urgent first."""
    from ..services import customers, inventory, reports

    out: list[str] = []
    month = reports.summary(conn, business, "month")
    if month.sales and month.expenses + month.cost_of_goods > month.sales:
        out.append("losing_money")
    if month.owner_draws and month.owner_draws > 0.3 * max(month.gross_profit, 1):
        out.append("owner_draws")
    debtors = customers.debtors(conn, business)
    if debtors and sum(d.balance for d in debtors) > 0.25 * max(month.sales, 1):
        out.append("many_debtors")
    if inventory.low_stock(conn, business):
        out.append("low_stock")
    if month.margin_pct is not None and month.margin_pct < 15:
        out.append("low_margin")
    if not month.has_activity:
        out.append("no_records")
    return out
