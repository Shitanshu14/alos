"""
Week 7 (extended) — Actually render a video file from the script.

content_generation_service.py writes a scene-by-scene script (text) for
content_type="video". This module turns that script into a REAL .mp4:
one slide image per scene (title + narration text) + offline
text-to-speech narration (espeak-ng via pyttsx3), stitched together with
moviepy/ffmpeg. Still no paid API, no internet call - everything runs
on this server for free.

Honesty about quality: this produces a simple "narrated slideshow" style
video (text on screen + a spoken-word voice), not hand-drawn animation.
That's the ceiling of what's achievable locally without a paid
AI video-generation service. If a real animated/illustrated video is
wanted later, generate_content()'s output (the script) is exactly the
brief a human animator or a paid AI video tool would need - swapping
this module for that later doesn't require changing anything upstream.

Requires system packages not guaranteed on every host: ffmpeg + espeak-ng.
If either is missing (e.g. a bare-bones free hosting tier), rendering
fails gracefully and the caller falls back to script-only content
instead of crashing the whole chapter pipeline.
"""
import logging
import re
import uuid
from pathlib import Path

logger = logging.getLogger("app.services.video_rendering_service")

SLIDE_SIZE = (1280, 720)
SLIDE_BG_COLOR = (30, 41, 59)      # dark slate
SLIDE_TEXT_COLOR = (255, 255, 255)
FONT_PATH_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
# DejaVu Sans has no Devanagari glyphs at all - Hindi text rendered as
# empty "tofu" boxes (confirmed via direct pixel-level testing: a
# rendered Hindi sentence had only 6 unique colors in its text region,
# vs 50-500+ for real glyphs). Noto Sans Devanagari covers Hindi/Marathi/
# Sanskrit text properly - used automatically when the slide text
# contains Devanagari script (see _get_font_for_text below).
DEVANAGARI_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
]
TITLE_FONT_SIZE = 44
BODY_FONT_SIZE = 32


class VideoRenderingError(Exception):
    """Raised when video rendering can't complete (missing system deps,
    bad script format, etc). Callers should treat this as non-fatal and
    fall back to script-only content."""


def _parse_scenes(script_body: str) -> list:
    """Pulls out 'Scene N (Label): text' lines from the generated script.
    Returns [{"label": str, "text": str}, ...]. Falls back to treating the
    whole script as one scene if the expected format isn't found."""
    scene_pattern = re.compile(r"Scene\s+\d+\s*(?:\([^)]*\))?\s*:\s*(.+)")
    scenes = []
    for line in script_body.splitlines():
        match = scene_pattern.search(line.strip())
        if match:
            scenes.append(match.group(1).strip())

    if not scenes:
        # Fallback: use non-empty lines that aren't the title/footer as scenes
        lines = [
            l.strip() for l in script_body.splitlines()
            if l.strip() and not l.strip().startswith("[") and "SCRIPT" not in l.upper()
        ]
        scenes = lines[:5] if lines else [script_body[:200]]

    return scenes[:6]  # cap length so the video doesn't get too long


def _contains_devanagari(text: str) -> bool:
    """Devanagari Unicode block is U+0900-U+097F (covers Hindi, Marathi,
    Sanskrit, etc)."""
    return any("\u0900" <= ch <= "\u097F" for ch in text)


def _get_font(size: int, text: str = ""):
    from PIL import ImageFont

    candidates = DEVANAGARI_FONT_CANDIDATES if _contains_devanagari(text) else FONT_PATH_CANDIDATES
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    # Fall back to the other script's font rather than PIL's tiny bitmap
    # default - better to show a mismatched-but-legible font than nothing.
    for path in FONT_PATH_CANDIDATES + DEVANAGARI_FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _split_script_runs(text: str) -> list:
    """Splits text into consecutive runs of the same script (Devanagari
    vs everything else), since no single installed font covers both -
    PIL has no automatic font-fallback the way a browser does, so mixed
    text needs to be drawn run-by-run with the right font each time."""
    runs = []
    current_is_dev = None
    current = ""
    for ch in text:
        is_dev = "\u0900" <= ch <= "\u097F"
        if current_is_dev is None:
            current_is_dev = is_dev
            current = ch
        elif is_dev == current_is_dev:
            current += ch
        else:
            runs.append((current, current_is_dev))
            current = ch
            current_is_dev = is_dev
    if current:
        runs.append((current, current_is_dev))
    return runs


def _text_width_mixed(draw, text: str, size: int) -> int:
    width = 0
    for run_text, is_dev in _split_script_runs(text):
        font = _get_font(size, text=run_text if is_dev else "")
        bbox = draw.textbbox((0, 0), run_text, font=font)
        width += bbox[2] - bbox[0]
    return width


def _draw_mixed_script_text(draw, xy, text: str, fill, size: int) -> None:
    x, y = xy
    for run_text, is_dev in _split_script_runs(text):
        font = _get_font(size, text=run_text if is_dev else "")
        draw.text((x, y), run_text, fill=fill, font=font)
        bbox = draw.textbbox((0, 0), run_text, font=font)
        x += bbox[2] - bbox[0]


def _wrap_text(text: str, size: int, max_width: int) -> list:
    """Word-wraps text to fit max_width, measuring each candidate line's
    width with the correct per-script font (see _text_width_mixed) so
    wrapping stays accurate even for mixed Hindi/English sentences."""
    from PIL import Image, ImageDraw
    dummy_img = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(dummy_img)

    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        width = _text_width_mixed(draw, candidate, size)
        if width <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _make_slide_image(scene_number: int, chapter_name: str, scene_text: str, out_path: str) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", SLIDE_SIZE, color=SLIDE_BG_COLOR)
    draw = ImageDraw.Draw(img)

    title = f"{chapter_name} — Scene {scene_number}"
    _draw_mixed_script_text(draw, (60, 50), title, fill=(147, 197, 253), size=TITLE_FONT_SIZE)  # light blue heading

    wrapped = _wrap_text(scene_text, BODY_FONT_SIZE, SLIDE_SIZE[0] - 120)
    y = 200
    for line in wrapped[:10]:
        _draw_mixed_script_text(draw, (60, y), line, fill=SLIDE_TEXT_COLOR, size=BODY_FONT_SIZE)
        y += BODY_FONT_SIZE + 16

    img.save(out_path)


def _narrate(text: str, out_path: str) -> None:
    import pyttsx3
    engine = pyttsx3.init()
    engine.setProperty("rate", 165)
    engine.save_to_file(text, out_path)
    engine.runAndWait()
    engine.stop()


def render_video(chapter_name: str, script_body: str, output_dir: str) -> str:
    """Renders the script into a real .mp4 file under output_dir/videos/.
    Returns the file's relative path (e.g. 'videos/abc123.mp4') for
    building a servable media_url. Raises VideoRenderingError on failure -
    callers should catch this and fall back to script-only content."""
    try:
        from moviepy import ImageClip, AudioFileClip, concatenate_videoclips
    except ImportError as exc:
        raise VideoRenderingError(f"moviepy not available: {exc}") from exc

    scenes = _parse_scenes(script_body)
    if not scenes:
        raise VideoRenderingError("Could not extract any scenes from the script")

    videos_dir = Path(output_dir) / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    work_id = uuid.uuid4().hex[:12]
    work_dir = videos_dir / f"_tmp_{work_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    clips = []
    final = None
    try:
        for i, scene_text in enumerate(scenes, start=1):
            slide_path = str(work_dir / f"slide_{i}.png")
            audio_path = str(work_dir / f"narration_{i}.wav")

            _make_slide_image(i, chapter_name, scene_text, slide_path)
            _narrate(scene_text, audio_path)

            audio_clip = AudioFileClip(audio_path)
            # Small pad so narration doesn't feel cut off
            duration = max(audio_clip.duration + 0.5, 1.5)
            image_clip = ImageClip(slide_path).with_duration(duration).with_audio(audio_clip)
            clips.append(image_clip)

        if not clips:
            raise VideoRenderingError("No scene clips were produced")

        final = concatenate_videoclips(clips, method="compose")
        output_filename = f"{work_id}.mp4"
        output_path = videos_dir / output_filename
        final.write_videofile(
            str(output_path), fps=24, codec="libx264", audio_codec="aac", logger=None,
        )

        return f"videos/{output_filename}"

    except Exception as exc:
        logger.exception("Video rendering failed for chapter '%s'", chapter_name)
        raise VideoRenderingError(f"Video rendering failed: {exc}") from exc
    finally:
        # Always close every clip - on both the success AND failure path.
        # Previously this only ran after a successful render, so a
        # mid-render failure (e.g. scene 3 of 5 erroring) left the
        # already-created clips' file handles open indefinitely.
        if final is not None:
            final.close()
        for c in clips:
            try:
                c.close()
            except Exception:
                pass  # best-effort - don't let cleanup itself raise
        # Clean up temp slide/audio files (keep only the final mp4)
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
