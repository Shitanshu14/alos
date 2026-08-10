"""
Week 7 — Content type decision logic + AI content generation.

When Week 6's matching finds nothing usable in the library for a
content type, this step creates new content instead - a story, poem,
video script, or cartoon script - built from the chapter's own key
concepts. Runs entirely locally (template + concept substitution, no
external AI API), same free-first approach as Week 4/6.

Honesty note: for "cartoon" we can't actually produce drawn artwork here -
we generate a structured panel-by-panel script as the item's body, with
no media_url, marked is_generated=True so admins know a real cartoon
still needs to be illustrated from the script.

For "video" specifically, we go one step further (see
video_rendering_service.py): the script is turned into a real, playable
.mp4 - simple narrated text slides via offline text-to-speech, not
animation - so "video" content actually has something to watch, not
just a script. If that rendering step fails for any reason (e.g. a
hosting environment missing ffmpeg/espeak-ng), it falls back to
script-only content automatically rather than breaking the pipeline.
"""
import re


def decide_content_types(chapter, suggested_content_types: list) -> list:
    """Week 7 — content type decision logic (age/subject based).
    Returns the 4 content types in priority order for this chapter, so
    the generation step (and any future UI) knows which types matter
    most for this age group / subject."""
    class_num = _parse_class_number(chapter.class_name)

    if class_num <= 3:
        # Younger students: short, rhythmic, visual formats first
        base_order = ["poem", "cartoon", "story", "video"]
    elif class_num <= 6:
        base_order = ["story", "cartoon", "poem", "video"]
    else:
        # Older students: denser explanatory formats first
        base_order = ["video", "story", "poem", "cartoon"]

    # Nudge anything Week 4's chapter-understanding step already flagged
    # as a good fit to the front, without dropping the other types.
    prioritized = [t for t in base_order if t in suggested_content_types]
    remaining = [t for t in base_order if t not in prioritized]
    return prioritized + remaining


def _parse_class_number(class_name: str) -> int:
    match = re.search(r"\d+", class_name or "")
    return int(match.group()) if match else 6  # default to a mid-range class if unparseable


def _concept(concepts: list, index: int, fallback: str) -> str:
    if concepts and index < len(concepts):
        return concepts[index]
    return fallback


def generate_content(chapter, content_type: str, key_concepts: list) -> dict:
    """Generates a new content item's fields for the given type.
    Returns {"title": str, "description": str, "body": str|None, "media_url": str|None}
    """
    if content_type == "story":
        return _generate_story(chapter, key_concepts)
    elif content_type == "poem":
        return _generate_poem(chapter, key_concepts)
    elif content_type == "video":
        return _generate_video_script(chapter, key_concepts)
    elif content_type == "cartoon":
        return _generate_cartoon_script(chapter, key_concepts)
    else:
        raise ValueError(f"Unknown content_type for generation: {content_type}")


def _generate_story(chapter, concepts: list) -> dict:
    c1 = _concept(concepts, 0, chapter.chapter_name)
    c2 = _concept(concepts, 1, "the topic")
    c3 = _concept(concepts, 2, c1)

    body = (
        f"On a school morning, a group of students sat around their teacher, eager to learn "
        f"about {chapter.chapter_name}. Their teacher started by explaining {c1}, using an "
        f"example everyone could picture in daily life. Then came {c2}, and the class began "
        f"to see how it connected back to {c1}. By the time they talked about {c3}, most of "
        f"the students could explain the idea in their own words. At the end of the lesson, "
        f"they wrote down three things they had learned about {chapter.chapter_name}, proud "
        f"of how much made sense now."
    )
    return {
        "title": f"{chapter.chapter_name} — A Story",
        "description": f"An auto-generated story introducing {chapter.chapter_name} through the concepts {c1} and {c2}.",
        "body": body,
        "media_url": None,
    }


def _generate_poem(chapter, concepts: list) -> dict:
    c1 = _concept(concepts, 0, chapter.chapter_name)
    c2 = _concept(concepts, 1, "each idea")
    c3 = _concept(concepts, 2, c1)

    body = (
        f"Come and learn about {chapter.chapter_name} today,\n"
        f"With {c1} leading the way.\n"
        f"{c2} joins in, step by step,\n"
        f"Building the knowledge that we'll keep.\n"
        f"Think of {c3}, picture it clear,\n"
        f"{chapter.chapter_name} makes sense - no need to fear!"
    )
    return {
        "title": f"{chapter.chapter_name} — A Poem",
        "description": f"An auto-generated rhyme to help students remember {c1} and {c2}.",
        "body": body,
        "media_url": None,
    }


def _generate_video_script(chapter, concepts: list) -> dict:
    c1 = _concept(concepts, 0, chapter.chapter_name)
    c2 = _concept(concepts, 1, "a related idea")
    c3 = _concept(concepts, 2, "a real-life example")

    body = (
        f"VIDEO SCRIPT — {chapter.chapter_name}\n\n"
        f"Scene 1 (Hook): Open with a relatable question or scenario that leads into {chapter.chapter_name}.\n"
        f"Scene 2 (Core idea): Explain {c1} step by step, using a simple on-screen diagram.\n"
        f"Scene 3 (Connection): Show how {c2} relates to {c1}, with a short visual example.\n"
        f"Scene 4 (Real-world link): Illustrate {c3} so students see why this matters outside class.\n"
        f"Scene 5 (Recap): Summarize the main points and leave students with one practice question.\n\n"
        f"[Auto-generated script - record/animate an actual video from this before publishing to students.]"
    )
    return {
        "title": f"{chapter.chapter_name} — Explainer Video (Script)",
        "description": f"An auto-generated video script covering {c1} and {c2} - needs to be produced into an actual video.",
        "body": body,
        "media_url": None,
    }


def _generate_cartoon_script(chapter, concepts: list) -> dict:
    c1 = _concept(concepts, 0, chapter.chapter_name)
    c2 = _concept(concepts, 1, "the next idea")
    c3 = _concept(concepts, 2, "a fun example")

    body = (
        f"CARTOON SCRIPT — {chapter.chapter_name}\n\n"
        f"Panel 1: A curious student character asks 'What is {chapter.chapter_name}?'\n"
        f"Panel 2: A friendly teacher character explains {c1} with a simple visual.\n"
        f"Panel 3: The student reacts, connecting {c1} to {c2}.\n"
        f"Panel 4: A quick, funny example showing {c3} in action.\n"
        f"Panel 5: Both characters recap what was learned about {chapter.chapter_name}.\n\n"
        f"[Auto-generated script - illustrate an actual cartoon strip from this before publishing to students.]"
    )
    return {
        "title": f"{chapter.chapter_name} — Cartoon Explainer (Script)",
        "description": f"An auto-generated cartoon script covering {c1} and {c2} - needs to be illustrated before publishing.",
        "body": body,
        "media_url": None,
    }


def validate_generated_content(content: dict) -> tuple:
    """Basic quality check before saving generated content to the library.
    Returns (is_valid: bool, reason: str)."""
    body = (content.get("body") or "").strip()
    if not body:
        return False, "Generated content has no body text"
    if len(body) < 40:
        return False, "Generated content body is too short to be useful"
    return True, "Generated content passed quality checks"
