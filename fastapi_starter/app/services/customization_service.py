"""
Week 6 — Content customization service (local, no external AI).

Once a content item is matched to a chapter, this personalizes its
title/description so a teacher/student can see *why* it was picked and
which of the chapter's concepts it covers - without regenerating or
rewriting the actual content (that's Week 7's job, for chapters with
no good match at all).
"""


def customize_for_chapter(item, chapter, matched_concepts: list) -> dict:
    """Returns {"title": str, "description": str} personalized for this chapter."""
    concepts_str = ", ".join(matched_concepts[:4]) if matched_concepts else "this chapter's key ideas"

    customized_title = f'{item.title} — for "{chapter.chapter_name}"'

    base_description = (item.description or "").strip()
    match_note = (
        f"Matched to your chapter '{chapter.chapter_name}' "
        f"({chapter.subject}, {chapter.class_name}) — covers: {concepts_str}."
    )
    customized_description = f"{base_description} {match_note}".strip() if base_description else match_note

    return {"title": customized_title, "description": customized_description}
