from afribiz import learning
from afribiz.learning import parse_module

SAMPLE = """# Module: Test module
order: 3

## First lesson
id: t-01
triggers: low_stock
topics: stock

Body text here.

**Try this:** Do something.

**Quiz:** What is 2 + 2?
- A) 3
- B) 4 *
**Why:** Basic maths.

## Second lesson

Only a body.
"""


def test_parse_module():
    lessons = parse_module(SAMPLE, "x/05-test.md")
    assert [lesson.id for lesson in lessons] == ["t-01", "05-test-02"]
    first = lessons[0]
    assert first.module == "Test module" and first.module_order == 3
    assert first.action == "Do something." and first.triggers == ["low_stock"]
    assert first.quiz.answer == "B" and first.quiz.explanation == "Basic maths."
    assert lessons[1].quiz is None


def test_shipped_content_is_valid(settings):
    lessons = [x for x in learning.load_lessons(settings.content_dir) if x.language == "en"]
    assert len(lessons) >= 20
    ids = [x.id for x in lessons]
    assert len(ids) == len(set(ids))
    for lesson in lessons:
        assert lesson.body and lesson.quiz, lesson.id
        assert lesson.quiz.answer in {letter for letter, _ in lesson.quiz.options}
        assert len(lesson.as_message()) < 1500, lesson.id  # fits comfortably in one WhatsApp message


def test_translations_replace_by_id(settings):
    sw = learning.find_lesson(settings.content_dir, "sw", "money-01")
    assert sw.language == "sw"
    # untranslated lessons fall back to English, keeping the full curriculum
    assert learning.find_lesson(settings.content_dir, "sw", "stock-01").language == "en"


def test_find_by_topic_and_number(settings):
    assert learning.find_lesson(settings.content_dir, "en", "pricing").id.startswith("pricing")
    assert learning.find_lesson(settings.content_dir, "en", "1").id == "money-01"
