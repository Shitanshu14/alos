"""
Week 5 — Seed 100+ pre-built content library items.

Generates a video/story/poem/cartoon item for each topic below, so the
library has decent coverage across subjects and classes to develop/test
Week 6 (matching) against. The story/poem text and video/cartoon links
are template-generated placeholders (marked is_seed_placeholder=True) -
NOT final content. Before pilot launch, an admin should review and
replace these via the /content-library admin endpoints.

Run standalone with:
    python -m app.seed_content_library

Idempotent: if the library already has items, it does nothing (so it's
safe to import/run more than once, e.g. from the admin "seed" endpoint).
"""
import logging

from .database import SessionLocal, engine, Base
from . import models

logger = logging.getLogger("app.seed_content_library")

# Each topic becomes 4 content items (video/story/poem/cartoon).
# 27 topics x 4 types = 108 items.
TOPICS = [
    # (subject, class_name, concept/topic)
    ("Science", "Class 3", "Living and Non-living Things"),
    ("Science", "Class 4", "Human Body - Digestive System"),
    ("Science", "Class 5", "States of Matter"),
    ("Science", "Class 6", "Photosynthesis"),
    ("Science", "Class 6", "Water Cycle"),
    ("Science", "Class 7", "Nutrition in Plants"),
    ("Science", "Class 7", "Acids and Bases"),
    ("Science", "Class 8", "Cell Structure"),
    ("Science", "Class 8", "Force and Pressure"),
    ("Math", "Class 2", "Shapes and Patterns"),
    ("Math", "Class 3", "Multiplication Tables"),
    ("Math", "Class 4", "Fractions"),
    ("Math", "Class 5", "Decimals"),
    ("Math", "Class 6", "Integers"),
    ("Math", "Class 7", "Algebraic Expressions"),
    ("Math", "Class 8", "Linear Equations"),
    ("English", "Class 3", "Nouns and Pronouns"),
    ("English", "Class 4", "Tenses"),
    ("English", "Class 5", "Adjectives"),
    ("English", "Class 6", "Story Writing Basics"),
    ("Social Studies", "Class 4", "Maps and Directions"),
    ("Social Studies", "Class 5", "Our Solar System"),
    ("Social Studies", "Class 6", "Indian Constitution Basics"),
    ("Social Studies", "Class 7", "Mughal Empire"),
    ("Social Studies", "Class 8", "French Revolution"),
    ("Hindi", "Class 4", "Sangya (Noun)"),
    ("Hindi", "Class 5", "Muhavare (Idioms)"),
]

CONTENT_TYPES = ["video", "story", "poem", "cartoon"]


def _slugify(text: str) -> str:
    return "-".join(text.lower().replace("(", "").replace(")", "").split())


def _tags_for(subject: str, class_name: str, concept: str) -> list[str]:
    return [subject.lower(), class_name.lower(), concept.lower()]


def _build_item(subject: str, class_name: str, concept: str, content_type: str) -> models.ContentItem:
    slug = _slugify(concept)

    if content_type == "video":
        item = models.ContentItem(
            title=f"{concept} — Explainer Video",
            content_type="video",
            subject=subject,
            class_name=class_name,
            description=f"A short animated video explaining {concept} for {class_name} students.",
            media_url=f"https://cdn.alos.app/videos/{slug}.mp4",
        )
    elif content_type == "cartoon":
        item = models.ContentItem(
            title=f"{concept} — Cartoon Explainer",
            content_type="cartoon",
            subject=subject,
            class_name=class_name,
            description=f"A fun cartoon strip that breaks {concept} down into simple visual steps.",
            media_url=f"https://cdn.alos.app/cartoons/{slug}.png",
        )
    elif content_type == "story":
        item = models.ContentItem(
            title=f"{concept} — A Story",
            content_type="story",
            subject=subject,
            class_name=class_name,
            description=f"A short story that introduces {concept} through an everyday example.",
            body=(
                f"Once upon a time, a curious student wanted to understand {concept}. "
                f"Their teacher broke it down step by step, using simple examples from "
                f"everyday life. By the end of the day, {concept} finally made sense, "
                f"and they couldn't wait to try a few practice questions on it."
            ),
        )
    else:  # poem
        item = models.ContentItem(
            title=f"{concept} — A Poem",
            content_type="poem",
            subject=subject,
            class_name=class_name,
            description=f"A short rhyme to help {class_name} students remember {concept}.",
            body=(
                f"Let's learn about {concept} today,\n"
                f"Step by step, in a simple way.\n"
                f"With {subject} ideas both big and small,\n"
                f"{concept} makes sense to us all."
            ),
        )

    item.is_seed_placeholder = True
    item.created_by = None  # system-seeded, not tied to an admin user
    return item


def seed_content_library(db, force: bool = False) -> int:
    """Inserts the seed content items. Returns how many were inserted.
    Skips only if the *seed* items already exist (checked via
    is_seed_placeholder=True) - unrelated content like Week 7's
    auto-generated items must not block this from running."""
    existing_count = (
        db.query(models.ContentItem)
        .filter(models.ContentItem.is_seed_placeholder == True)  # noqa: E712
        .count()
    )
    if existing_count > 0 and not force:
        logger.info("Content library already has %s seed item(s) - skipping seed.", existing_count)
        return 0

    # Cache tags so the same concept/subject/class tag is reused across items
    tag_cache: dict[str, models.ContentTag] = {}

    def get_tag(name: str) -> models.ContentTag:
        if name not in tag_cache:
            tag = db.query(models.ContentTag).filter(models.ContentTag.name == name).first()
            if not tag:
                tag = models.ContentTag(name=name)
                db.add(tag)
                db.flush()
            tag_cache[name] = tag
        return tag_cache[name]

    inserted = 0
    for subject, class_name, concept in TOPICS:
        tag_names = _tags_for(subject, class_name, concept)
        tags = [get_tag(name) for name in tag_names]

        for content_type in CONTENT_TYPES:
            item = _build_item(subject, class_name, concept, content_type)
            item.tags = tags
            db.add(item)
            inserted += 1

    db.commit()
    logger.info("Seeded %s content library item(s) across %s topics.", inserted, len(TOPICS))
    return inserted


if __name__ == "__main__":
    logging_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=logging_format)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        count = seed_content_library(db)
        print(f"Inserted {count} content library item(s).")
    finally:
        db.close()
