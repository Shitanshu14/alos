"""
Week 13 — Concept-matching + memory game data generation.

Builds game data directly from a chapter's ChapterBrief (Week 4's key
concepts) - no external API, fully local and free like the rest of the
pipeline. Generated once per chapter and cached in GameTemplate so
repeat requests (every student who opens the game) don't regenerate it.
"""
import json
import random


def build_concept_match_pairs(chapter, key_concepts: list, summary: str) -> list:
    """Each pair is {"concept": str, "clue": str} - the clue is the first
    sentence of the chapter summary that mentions the concept, falling
    back to a generic clue if none mentions it directly."""
    summary_sentences = [s.strip() for s in summary.replace("\n", " ").split(".") if s.strip()]

    pairs = []
    for concept in key_concepts[:8]:  # cap game size to keep it playable
        clue = next(
            (s for s in summary_sentences if concept.lower() in s.lower()),
            f"A concept from the chapter '{chapter.chapter_name}' ({chapter.subject}, {chapter.class_name}).",
        )
        pairs.append({"concept": concept, "clue": clue})
    return pairs


def build_memory_cards(key_concepts: list) -> list:
    """Each concept becomes two cards (a matching pair), pre-shuffled so
    the frontend can render them directly without its own shuffle logic."""
    concepts = key_concepts[:8]  # 8 concepts = 16 cards, a reasonable memory-game size
    cards = []
    for i, concept in enumerate(concepts):
        cards.append({"card_id": f"{i}-a", "concept": concept})
        cards.append({"card_id": f"{i}-b", "concept": concept})
    random.shuffle(cards)
    return cards


def serialize(data: list) -> str:
    return json.dumps(data)


def deserialize(data: str) -> list:
    return json.loads(data)
