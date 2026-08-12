"""
Week 14 — Quiz generation (5 questions per chapter).

Builds multiple-choice questions from a chapter's key concepts (Week 4's
ChapterBrief) - local template generation, no external API. Each
question asks the learner to pick which concept belongs to this
chapter; wrong options ("distractors") are pulled from other chapters'
concepts so they're plausible but incorrect.

Honesty note: these are recognition-style questions ("which of these was
covered in this chapter"), not deep comprehension questions - that's the
ceiling of what's reliably generatable without a real language model
understanding the chapter's meaning.
"""
import random

import json
import random

from ..models import Chapter, ChapterBrief

QUESTIONS_PER_QUIZ = 5
OPTIONS_PER_QUESTION = 4


def _get_distractor_pool(db, chapter: Chapter, exclude_concepts: set) -> list:
    """Pulls key concepts from *other* chapters (same subject preferred,
    any subject as a fallback) to use as wrong-answer options."""
    briefs = (
        db.query(ChapterBrief)
        .join(Chapter, Chapter.id == ChapterBrief.chapter_id)
        .filter(Chapter.id != chapter.id, Chapter.subject == chapter.subject)
        .all()
    )
    if not briefs:
        briefs = db.query(ChapterBrief).filter(ChapterBrief.chapter_id != chapter.id).all()

    pool = []
    for brief in briefs:
        try:
            concepts = json.loads(brief.key_concepts)
        except (TypeError, ValueError):
            continue
        pool.extend(c for c in concepts if c not in exclude_concepts)

    return list(dict.fromkeys(pool))  # dedupe, preserve order


_GENERIC_DISTRACTORS = [
    "None of these", "A completely unrelated topic", "Something from a different subject", "An unrelated fact",
]


def generate_quiz_questions(db, chapter: Chapter, key_concepts: list) -> list:
    """Returns a list of dicts: [{"question_text": str, "options": [4 strings],
    "correct_index": int}, ...] - up to QUESTIONS_PER_QUIZ questions."""
    concepts = key_concepts[:QUESTIONS_PER_QUIZ]
    if not concepts:
        return []

    distractor_pool = _get_distractor_pool(db, chapter, exclude_concepts=set(concepts))

    questions = []
    for concept in concepts:
        needed = OPTIONS_PER_QUESTION - 1
        distractors = random.sample(distractor_pool, min(needed, len(distractor_pool)))
        # Pad with generic distractors if the library doesn't have enough real ones yet
        while len(distractors) < needed:
            filler = random.choice(_GENERIC_DISTRACTORS)
            if filler not in distractors:
                distractors.append(filler)

        options = distractors + [concept]
        random.shuffle(options)
        correct_index = options.index(concept)

        questions.append({
            "question_text": f"Which of these was covered in the chapter '{chapter.chapter_name}'?",
            "options": options,
            "correct_index": correct_index,
        })

    return questions
