"""
Week 4 — Chapter understanding via local text analysis (NO external AI API).

This originally called the Anthropic API for chapter understanding, but
that costs money per request and comes with rate limits. This version
does the same job - a summary, key concepts, and suggested content
types - using classic, well-understood NLP techniques (word-frequency
scoring + n-gram extraction) that run entirely on this server. No API
key, no external calls, no per-request cost, no usage limits.

Trade-off: it doesn't "understand" the chapter the way an LLM would -
it statistically finds the most important sentences/words. In practice
this works reasonably well for structured textbook chapters, and it's
free to run as many times as needed.

Keeps the exact same function signature and return shape as the old
Anthropic-backed version, so nothing else in the codebase
(chapters_router.py) needs to change.
"""
import re
import logging
from collections import Counter

logger = logging.getLogger("app.services.ai_service")


class AIServiceError(Exception):
    """Raised when chapter analysis can't produce usable output
    (e.g. the chapter text is empty or has no meaningful words)."""


# A small built-in stopword list - no external downloads needed at runtime
# (unlike nltk, which needs to fetch data files the first time it's used).
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "and", "or", "but",
    "if", "then", "so", "of", "to", "in", "on", "at", "for", "with", "by", "from", "up", "down",
    "into", "over", "under", "again", "further", "once", "here", "there", "when", "where",
    "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "than", "too", "very", "can", "will", "just",
    "should", "now", "this", "that", "these", "those", "it", "its", "as", "also", "which",
    "who", "whom", "what", "i", "we", "you", "he", "she", "they", "them", "his", "her",
    "their", "our", "your", "my", "me", "us", "him", "do", "does", "did", "have", "has",
    "had", "having", "because", "about", "between", "through", "during", "before", "after",
    "above", "below", "out", "off", "while", "one", "two", "three", "using", "used", "use",
}

# Common verbs / adverbs / filler that are frequent in textbook prose but are
# never a "concept" worth teaching (e.g. 'produced', 'occurs', 'mainly').
STOPWORDS |= {
    "produce", "produced", "produces", "producing", "occur", "occurs", "occurred", "mainly",
    "make", "makes", "made", "making", "take", "takes", "taken", "taking", "release", "released",
    "releases", "absorb", "absorbed", "absorbs", "called", "known", "get", "gets", "got", "give",
    "gives", "given", "many", "much", "often", "usually", "form", "forms", "formed", "found",
    "help", "helps", "like", "need", "needs", "needed", "become", "becomes", "became", "called",
    "example", "examples", "called", "way", "ways", "something", "thing", "things", "may", "might",
    "must", "could", "would", "every", "another", "called", "includes", "include", "including",
    "called", "see", "seen", "show", "shows", "shown", "called", "keep", "keeps", "called",
}

# Hindi function words (Devanagari). Without these, the most "frequent words"
# of a Hindi chapter would just be के / है / में / और.
STOPWORDS |= {
    "का", "के", "की", "में", "है", "हैं", "और", "से", "को", "पर", "यह", "वह", "एक", "था", "थे",
    "थी", "कि", "इस", "उस", "जो", "तो", "भी", "ही", "कर", "करते", "करता", "करती", "होता", "होती",
    "होते", "लिए", "द्वारा", "साथ", "बाद", "या", "जब", "तब", "हम", "आप", "वे", "इन", "उन", "नहीं",
    "कहते", "कहलाता", "कहलाती", "बनाते", "हुआ", "हुई", "हुए", "गया", "गई", "गए", "सकते", "सकता",
    "सकती", "लेकिन", "अपना", "अपने", "अपनी", "बहुत", "कुछ", "सभी", "तक", "वाले", "वाला", "वाली",
    "रहा", "रही", "रहे", "क्या", "कैसे", "जाता", "जाती", "जाते", "देता", "देते", "लेता", "लेते",
    "जैसे", "इसे", "उसे", "इसके", "उसके", "इसका", "उसका", "इसकी", "उसकी", "होना", "करना",
    "चाहिए", "ऐसे", "इसलिए", "क्योंकि", "अगर", "जिसे", "जिसमें", "जिससे", "वाले",
}

# Sentence enders: . ! ? and the Devanagari danda (।) / double danda (॥).
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\u0964\u0965])\s+")
# A word is a run of Latin letters OR Devanagari letters + vowel signs
# (U+0900-U+0963, U+0971-U+097F; this skips the danda U+0964/5 and Devanagari
# digits U+0966-U+096F). The old pattern was Latin-only, so a Hindi chapter had
# "no meaningful words" and the whole pipeline failed for it.
_LETTER = r"A-Za-z\u0900-\u0963\u0971-\u097F"
WORD_RE = re.compile(rf"[{_LETTER}][{_LETTER}\-']+")


def _split_sentences(text: str) -> list:
    cleaned = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(cleaned) if s.strip()]


def _word_frequencies(sentences: list) -> Counter:
    freq = Counter()
    for sentence in sentences:
        for word in WORD_RE.findall(sentence.lower()):
            if word not in STOPWORDS and len(word) > 2:
                freq[word] += 1
    return freq


def _extractive_summary(sentences: list, freq: Counter, max_sentences: int = 4) -> str:
    """Picks the most 'important' sentences (by word-frequency score) and
    joins them in original order - a standard extractive summarization
    technique that needs no external model."""
    if not sentences:
        return ""

    scored = []
    for i, sentence in enumerate(sentences):
        words = [w for w in WORD_RE.findall(sentence.lower()) if w not in STOPWORDS]
        score = (sum(freq.get(w, 0) for w in words) / len(words)) if words else 0
        # Chapters usually open with the main idea - small boost for early sentences
        position_boost = 1.15 if i < 2 else 1.0
        scored.append((score * position_boost, i, sentence))

    top = sorted(scored, key=lambda x: x[0], reverse=True)[:max_sentences]
    top_in_order = sorted(top, key=lambda x: x[1])  # restore original reading order
    return " ".join(s for _, _, s in top_in_order)


def _extract_key_concepts(sentences: list, freq: Counter, max_concepts: int = 8) -> list:
    """Bigrams catch multi-word concepts (e.g. 'water cycle') that plain
    single-word frequency would miss; combined with top single words.
    Prefers words/phrases that repeat, but falls back to the most frequent
    ones even if they only appear once - short chapters (few sentences)
    would otherwise never produce any concepts at all."""
    bigram_counts = Counter()
    for sentence in sentences:
        words = [w for w in WORD_RE.findall(sentence.lower()) if w not in STOPWORDS and len(w) > 2]
        for i in range(len(words) - 1):
            bigram_counts[f"{words[i]} {words[i + 1]}"] += 1

    top_bigrams = [phrase for phrase, count in bigram_counts.most_common(max_concepts) if count > 1]
    top_unigrams = [word for word, count in freq.most_common(max_concepts) if count > 1]

    # Fallback for short/sparse text where nothing repeats: just take the
    # most frequent words/phrases regardless of count.
    if not top_bigrams and not top_unigrams:
        top_bigrams = [phrase for phrase, _ in bigram_counts.most_common(max_concepts // 2)]
        top_unigrams = [word for word, _ in freq.most_common(max_concepts)]

    concepts = []
    for phrase in top_bigrams + top_unigrams:
        titled = phrase.title()
        if titled not in concepts:
            concepts.append(titled)
        if len(concepts) >= max_concepts:
            break
    return concepts


def _suggest_content_types(word_count: int) -> list:
    # Simple heuristic: shorter/simpler chapters suit poems & cartoons well;
    # longer/denser ones suit video & story formats that hold more detail.
    if word_count < 300:
        return ["poem", "cartoon", "story"]
    return ["video", "story", "cartoon"]


def analyze_chapter_text(chapter_text: str) -> dict:
    """Local (no external API) chapter analyzer. Same interface/return shape
    as the old Anthropic-backed version:
    {"summary": str, "key_concepts": list[str], "suggested_content_types": list[str]}
    """
    sentences = _split_sentences(chapter_text)
    if not sentences:
        raise AIServiceError("Chapter text has no readable sentences to analyze")

    freq = _word_frequencies(sentences)
    if not freq:
        raise AIServiceError("Chapter text has no meaningful words to analyze")

    summary = _extractive_summary(sentences, freq)
    key_concepts = _extract_key_concepts(sentences, freq)
    if not key_concepts:
        raise AIServiceError("Could not extract any key concepts from this chapter")

    word_count = sum(freq.values())
    suggested_content_types = _suggest_content_types(word_count)

    return {
        "summary": summary,
        "key_concepts": key_concepts,
        "suggested_content_types": suggested_content_types,
        # Extra (not stored on the brief): the chapter's own best sentence for
        # each concept, so the video script can teach real content instead of
        # a generic template. See content_generation_service._generate_video_script.
        "concept_sentences": _pick_concept_sentences(sentences, key_concepts),
        "opening_sentence": _clean_sentence(sentences[0]) if sentences else "",
    }


MAX_NARRATION_SENTENCE_CHARS = 220


def _clean_sentence(sentence: str) -> str:
    """Trims a sentence to a length that fits a slide / reads well aloud,
    cutting at a clause boundary (comma/semicolon) when it is too long."""
    sentence = re.sub(r"\s+", " ", sentence).strip()
    if len(sentence) <= MAX_NARRATION_SENTENCE_CHARS:
        return sentence
    cut = max(sentence.rfind(",", 0, MAX_NARRATION_SENTENCE_CHARS), sentence.rfind(";", 0, MAX_NARRATION_SENTENCE_CHARS))
    if cut < 80:
        cut = sentence.rfind(" ", 0, MAX_NARRATION_SENTENCE_CHARS)
    return sentence[:cut].rstrip(",; ") + "…"


def _pick_concept_sentences(sentences: list, key_concepts: list, max_items: int = 5) -> list:
    """For each key concept, picks the sentence that best explains it:
    one that mentions the concept (all of its words), preferring a medium-length,
    definition-like sentence, and never reusing a sentence for two concepts.
    Returns [{"concept": str, "sentence": str}, ...] (concepts with no
    sentence are skipped)."""
    used = set()
    picked = []
    picked_wordsets = []
    for concept in key_concepts:
        words = [w.lower() for w in WORD_RE.findall(concept)]
        if not words:
            continue
        # 'Sunlight' after 'Sunlight Plants' (or 'सूर्य' after 'सूर्य प्रकाश') would
        # be the same idea taught twice - skip concepts already covered.
        if any(set(words) <= prev for prev in picked_wordsets):
            continue
        best_idx, best_score = None, 0.0
        for i, sentence in enumerate(sentences):
            if i in used:
                continue
            lowered = sentence.lower()
            if not all(w in lowered for w in words):
                continue
            length = len(sentence)
            # Prefer 50-200 chars; mild bonus for appearing early in the sentence
            # (definitions usually start "X is ...") and for earlier position.
            score = 1.0
            if 50 <= length <= 200:
                score += 1.0
            elif length < 25:
                score -= 0.8
            pos = lowered.find(words[0])
            if pos != -1 and pos < 25:
                score += 0.5
            score += max(0.0, 0.3 - i * 0.02)
            if score > best_score:
                best_idx, best_score = i, score
        if best_idx is not None:
            used.add(best_idx)
            picked_wordsets.append(set(words))
            picked.append({"concept": concept, "sentence": _clean_sentence(sentences[best_idx])})
        if len(picked) >= max_items:
            break
    return picked
