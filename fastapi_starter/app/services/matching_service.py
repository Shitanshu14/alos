"""
Week 6 — Concept matching algorithm + best-match selection logic.

Takes a chapter's key concepts (from its ChapterBrief, Week 4) and finds
the best existing ContentItem in the library (Week 5) for each content
type (video/story/poem/cartoon), scored by how well its subject/class/
tags overlap with the chapter. Runs entirely locally - no external API,
same free-first approach as Week 4's chapter understanding.

If nothing scores high enough for a given content type, that type comes
back with item=None - a clean signal for Week 7 (content generation) to
create something new instead of forcing a bad-fit match.
"""
import logging

from sqlalchemy.orm import joinedload

from ..models import ContentItem

logger = logging.getLogger("app.services.matching_service")

CONTENT_TYPES = ["video", "story", "poem", "cartoon"]

# Minimum score (0-1) a content item must reach to count as a real match.
# Below this, we treat it as "no match" rather than serving a poor fit.
MIN_MATCH_SCORE = 0.25

# Scoring weights (must sum to 1.0)
SUBJECT_WEIGHT = 0.3
CLASS_WEIGHT = 0.2
CONCEPT_OVERLAP_WEIGHT = 0.5


def _normalize(text: str) -> str:
    return text.strip().lower()


def _concept_matches_tag(concept: str, tag: str) -> bool:
    # A concept "matches" a tag if either contains the other - handles
    # e.g. chapter concept "Water Cycle" vs library tags "water cycle" or "cycle".
    return concept == tag or concept in tag or tag in concept


def _matched_concepts(chapter_concepts: list, item_tag_names: set) -> list:
    matched = []
    for concept in chapter_concepts:
        cn = _normalize(concept)
        if any(_concept_matches_tag(cn, tag) for tag in item_tag_names):
            matched.append(concept)
    return matched


def score_item(chapter_subject: str, chapter_class: str, chapter_concepts: list, item: ContentItem) -> float:
    """Returns a 0-1 score for how well this content item fits the chapter."""
    score = 0.0

    if _normalize(item.subject) == _normalize(chapter_subject):
        score += SUBJECT_WEIGHT
    if _normalize(item.class_name) == _normalize(chapter_class):
        score += CLASS_WEIGHT

    item_tag_names = {t.name for t in item.tags}
    chapter_concept_names = [c for c in chapter_concepts]

    if item_tag_names and chapter_concept_names:
        matched = _matched_concepts(chapter_concept_names, item_tag_names)
        overlap_ratio = len(matched) / max(len(chapter_concept_names), 1)
        score += overlap_ratio * CONCEPT_OVERLAP_WEIGHT

    return round(min(score, 1.0), 4)


def find_best_matches(db, chapter, chapter_concepts: list) -> dict:
    """Returns, for each of the 4 content types:
    {"item": ContentItem|None, "score": float, "matched_concepts": list[str]}
    item is None when nothing reached MIN_MATCH_SCORE for that type."""
    candidates = (
        db.query(ContentItem)
        .options(joinedload(ContentItem.tags))
        .filter(ContentItem.subject == chapter.subject)
        .all()
    )
    # Fall back to the whole library if nothing matches the subject exactly
    # (e.g. a naming mismatch like "Social Studies" vs "Social Science")
    if not candidates:
        candidates = db.query(ContentItem).options(joinedload(ContentItem.tags)).all()

    results = {}
    for content_type in CONTENT_TYPES:
        type_candidates = [c for c in candidates if c.content_type == content_type]
        if content_type == "video":
            # The seeded library's video items are placeholders: their
            # media_url points at a fake CDN (cdn.alos.app) and no such file
            # exists. Matching one made the chapter's "video" a dead link and
            # skipped rendering entirely. Only real videos - admin-uploaded
            # or rendered by video_rendering_service - can win a match.
            type_candidates = [c for c in type_candidates if not c.is_seed_placeholder]

        best_item = None
        best_score = 0.0
        for item in type_candidates:
            s = score_item(chapter.subject, chapter.class_name, chapter_concepts, item)
            if s > best_score:
                best_score = s
                best_item = item

        if best_item is not None and best_score >= MIN_MATCH_SCORE:
            matched = _matched_concepts(chapter_concepts, {t.name for t in best_item.tags})
            results[content_type] = {"item": best_item, "score": best_score, "matched_concepts": matched}
        else:
            results[content_type] = {"item": None, "score": best_score, "matched_concepts": []}

    return results


def validate_match(item: ContentItem, score: float):
    """Quality check gate (Week 6) run before a match is finalized.
    Returns (is_valid: bool, reason: str)."""
    if item is None:
        return False, "No content item in the library met the minimum match score"
    if score < MIN_MATCH_SCORE:
        return False, f"Match score {score} is below the minimum threshold {MIN_MATCH_SCORE}"
    if item.content_type in ("story", "poem") and not (item.body and item.body.strip()):
        return False, "Matched text content has no body"
    if item.content_type in ("video", "cartoon") and not (item.media_url and item.media_url.strip()):
        return False, "Matched media content has no media_url"
    if item.content_type == "video" and item.is_seed_placeholder:
        return False, "Matched video is a seed placeholder (its media_url is not a real file)"
    if not item.tags:
        return False, "Matched item has no tags - too low-confidence to finalize"
    return True, "Match passed quality checks"


def evaluate_matches(matches: dict) -> dict:
    """Runs validate_match over every entry from find_best_matches().
    Returns the same dict shape with "is_valid" and "reason" added to each."""
    evaluated = {}
    for content_type, result in matches.items():
        is_valid, reason = validate_match(result["item"], result["score"])
        evaluated[content_type] = {**result, "is_valid": is_valid, "reason": reason}
    return evaluated


def get_unmatched_content_types(evaluated_matches: dict) -> list:
    """Week 7 — no-match detection logic: returns the content types where
    the library had nothing valid, meaning Week 7's generation step needs
    to create new content for them instead."""
    return [ct for ct, r in evaluated_matches.items() if not r["is_valid"]]
