"""
Week 7 (extended) — Render a real, playable video from a generated script.

content_generation_service.py writes a scene-by-scene script for
content_type="video". This module turns that script into a REAL .mp4:
one designed slide per scene (chapter header, scene label, auto-sized body
text, progress bar) + offline text-to-speech narration (espeak-ng), encoded
with ffmpeg. No paid API and no internet call - everything runs on this
server for free.

What a render produces (all under <output_dir>/videos/, same base name):
    <id>.mp4   the video (H.264 + AAC, `faststart` so playback begins before the
               whole file downloads)
    <id>.vtt   WebVTT captions, one cue per scene (accessibility; <track> tag)
    <id>.jpg   poster frame (first slide) for <video poster="...">

Changes vs. the original moviepy version:
  * ffmpeg is called directly (system ffmpeg, falling back to the binary that
    ships with the `imageio-ffmpeg` wheel). moviepy was dropped: it was slow
    for what are mostly static frames, and it pinned Pillow to a release with
    dozens of known CVEs.
  * At most MAX_CONCURRENT_RENDERS renders run at once (a semaphore). Each
    render is CPU/RAM heavy and the pipeline runs in an in-process thread pool;
    unbounded concurrency would OOM a small instance.

Honesty about quality: this is a "narrated slideshow" style video, not
hand-drawn animation - the ceiling of what is achievable locally without a
paid AI video service. The script (see content_generation_service) is the
brief such a service - or a human animator - would need, so swapping this
module out later changes nothing upstream.

If a render fails, VideoRenderingError is raised and the caller falls back to
script-only content instead of crashing the chapter pipeline.
"""
import logging
import re
import shutil
import subprocess
import threading
import uuid
import wave
from functools import lru_cache
from pathlib import Path

from ..config import settings

logger = logging.getLogger("app.services.video_rendering_service")

SLIDE_SIZE = (1280, 720)
FPS = 15                       # slides are static; low fps keeps files tiny
MAX_SCENES = 8                 # keeps videos short enough for a student's attention span
NARRATION_WORDS_PER_MINUTE = 150   # a little slower than espeak's default: for learners

BG_COLOR = (15, 23, 42)           # slate-900
PANEL_COLOR = (30, 41, 59)        # slate-800 (header band / progress track)
SLIDE_BG_COLOR = BG_COLOR         # kept for backwards compatibility
SLIDE_TEXT_COLOR = (241, 245, 249)
HEADER_TEXT_COLOR = (203, 213, 225)
CHIP_TEXT_COLOR = (15, 23, 42)
ACCENTS = {
    "intro": (96, 165, 250),      # blue
    "overview": (167, 139, 250),  # violet
    "concept": (52, 211, 153),    # green
    "recap": (251, 191, 36),      # amber
    "default": (147, 197, 253),
}

FONT_PATH_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # Linux / the Docker image
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",      # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arialbd.ttf",                           # Windows
    "C:/Windows/Fonts/arial.ttf",
]
# DejaVu Sans has no Devanagari glyphs - Hindi rendered as empty "tofu" boxes.
# Noto Sans Devanagari is used automatically for Devanagari text runs.
DEVANAGARI_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/System/Library/Fonts/Supplemental/DevanagariMT.ttc",    # macOS
    "/System/Library/Fonts/Kohinoor.ttc",
    "C:/Windows/Fonts/Nirmala.ttf",                           # Windows
]
TITLE_FONT_SIZE = 40
BODY_FONT_SIZES = (46, 42, 38, 34, 30, 27, 24)   # tried largest-first until the text fits
BODY_TOP = 215
BODY_BOTTOM = 650

# Bounds how many renders run at once (see module docstring).
_render_slots = threading.BoundedSemaphore(max(1, int(getattr(settings, "MAX_CONCURRENT_RENDERS", 1))))


class VideoRenderingError(Exception):
    """Raised when video rendering can't complete (missing system deps,
    bad script format, etc). Callers should treat this as non-fatal and
    fall back to script-only content."""


# --------------------------------------------------------------------------- #
# Script parsing
# --------------------------------------------------------------------------- #
_SCENE_RE = re.compile(r"Scene\s+\d+\s*(?:\(([^)]*)\))?\s*:\s*(.+)")


def _parse_scene_details(script_body: str) -> list:
    """Pulls 'Scene N (Label): text' lines out of the generated script.
    Returns [{"label": str, "text": str}, ...]; falls back to treating
    non-empty lines as scenes if the expected format isn't found."""
    scenes = []
    for line in script_body.splitlines():
        m = _SCENE_RE.search(line.strip())
        if m:
            scenes.append({"label": (m.group(1) or "").strip(), "text": m.group(2).strip()})

    if not scenes:
        lines = [
            l.strip() for l in script_body.splitlines()
            if l.strip() and not l.strip().startswith("[") and "SCRIPT" not in l.upper()
        ]
        scenes = [{"label": "", "text": t} for t in (lines[:5] if lines else [script_body[:200]])]

    return scenes[:MAX_SCENES]


def _parse_scenes(script_body: str) -> list:
    """Scene texts only (kept for backwards compatibility / tests)."""
    return [s["text"] for s in _parse_scene_details(script_body)]


def _scene_kind(label: str) -> str:
    l = (label or "").lower()
    if l.startswith(("intro", "परिचय")):
        return "intro"
    if l.startswith(("what we", "overview", "इस पाठ")):
        return "overview"
    if l.startswith(("key idea", "मुख्य")):
        return "concept"
    if l.startswith(("recap", "दोहराव")):
        return "recap"
    return "default"


# --------------------------------------------------------------------------- #
# Fonts / text layout (per-script font choice - see Devanagari note above)
# --------------------------------------------------------------------------- #
def _contains_devanagari(text: str) -> bool:
    """Devanagari Unicode block is U+0900-U+097F (Hindi, Marathi, Sanskrit...)."""
    return any("\u0900" <= ch <= "\u097F" for ch in text)


@lru_cache(maxsize=64)
def _load_truetype(path: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(path, size)


def _get_font(size: int, text: str = ""):
    from PIL import ImageFont

    candidates = DEVANAGARI_FONT_CANDIDATES if _contains_devanagari(text) else FONT_PATH_CANDIDATES
    for path in candidates:
        if Path(path).exists():
            return _load_truetype(path, size)
    # Fall back to the other script's font rather than PIL's tiny bitmap default.
    for path in FONT_PATH_CANDIDATES + DEVANAGARI_FONT_CANDIDATES:
        if Path(path).exists():
            return _load_truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _split_script_runs(text: str) -> list:
    """Splits text into consecutive runs of the same script (Devanagari vs
    everything else): no single installed font covers both and PIL has no
    automatic font-fallback, so mixed text is drawn run-by-run."""
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
    """Word-wraps text to max_width using the correct per-script font."""
    from PIL import Image, ImageDraw
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if _text_width_mixed(draw, candidate, size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_single_line(draw, text: str, start_size: int, min_size: int, max_width: int):
    """Largest size (down to min_size) at which `text` fits on one line;
    truncates with an ellipsis if even min_size is too wide."""
    size = start_size
    while size > min_size and _text_width_mixed(draw, text, size) > max_width:
        size -= 2
    if _text_width_mixed(draw, text, size) > max_width:
        while len(text) > 1 and _text_width_mixed(draw, text + "…", size) > max_width:
            text = text[:-1]
        text = text.rstrip() + "…"
    return text, size


def _make_slide_image(scene_number: int, chapter_name: str, scene_text: str, out_path: str,
                      *, total: int = None, label: str = "") -> None:
    """Draws one 1280x720 slide: header band with the chapter name and 'n / total',
    a coloured label chip, body text auto-shrunk to fit, and a progress bar."""
    from PIL import Image, ImageDraw

    total = total or scene_number
    accent = ACCENTS[_scene_kind(label)]
    img = Image.new("RGB", SLIDE_SIZE, color=BG_COLOR)
    draw = ImageDraw.Draw(img)
    width, height = SLIDE_SIZE

    # Header band
    draw.rectangle([0, 0, width, 108], fill=PANEL_COLOR)
    counter = f"{scene_number} / {total}"
    counter_w = _text_width_mixed(draw, counter, 28)
    title, title_size = _fit_single_line(draw, chapter_name, TITLE_FONT_SIZE, 26, width - 120 - counter_w - 40)
    _draw_mixed_script_text(draw, (60, 26), title, fill=HEADER_TEXT_COLOR, size=title_size)
    _draw_mixed_script_text(draw, (width - 60 - counter_w, 36), counter, fill=accent, size=28)

    # Label chip
    y = 140
    if label:
        label_text, label_size = _fit_single_line(draw, label.upper(), 24, 18, width - 200)
        chip_w = _text_width_mixed(draw, label_text, label_size) + 44
        draw.rounded_rectangle([60, y, 60 + chip_w, y + 46], radius=23, fill=accent)
        _draw_mixed_script_text(draw, (82, y + 6), label_text, fill=CHIP_TEXT_COLOR, size=label_size)

    # Body: largest font size at which the text fits the body box
    max_width = width - 160
    box_height = BODY_BOTTOM - BODY_TOP
    chosen_lines, chosen_size = None, BODY_FONT_SIZES[-1]
    for size in BODY_FONT_SIZES:
        line_h = int(size * 1.4)
        lines = _wrap_text(scene_text, size, max_width)
        if len(lines) * line_h <= box_height:
            chosen_lines, chosen_size = lines, size
            break
    if chosen_lines is None:
        size = BODY_FONT_SIZES[-1]
        max_lines = max(1, box_height // int(size * 1.4))
        lines = _wrap_text(scene_text, size, max_width)
        chosen_lines, chosen_size = lines[:max_lines], size
        chosen_lines[-1] = chosen_lines[-1].rstrip(" .,;") + "…"

    line_h = int(chosen_size * 1.4)
    block_h = len(chosen_lines) * line_h
    # Centre the text block vertically in the body area (short scenes otherwise
    # leave the lower half of the slide empty); nudged up slightly for balance.
    ty = BODY_TOP + max(0, (box_height - block_h) // 2 - 30)
    draw.rectangle([60, ty, 68, ty + block_h - 8], fill=accent)  # accent rule
    for line in chosen_lines:
        _draw_mixed_script_text(draw, (96, ty), line, fill=SLIDE_TEXT_COLOR, size=chosen_size)
        ty += line_h

    # Progress bar
    draw.rectangle([0, height - 14, width, height], fill=PANEL_COLOR)
    draw.rectangle([0, height - 14, int(width * scene_number / max(total, 1)), height], fill=accent)

    img.save(out_path)


# --------------------------------------------------------------------------- #
# Narration
# --------------------------------------------------------------------------- #
def narration_available() -> bool:
    """True if a text-to-speech engine (espeak-ng) is installed on this host."""
    return bool(shutil.which("espeak-ng") or shutil.which("espeak"))


def _silent_duration(text: str) -> float:
    """How long to show a slide with no narration: ~reading speed, min 4 s."""
    return max(len(text.split()) / 2.5 + 1.5, 4.0)


def _narrate(text: str, out_path: str) -> None:
    """Text-to-speech via the espeak-ng CLI -> a .wav file. (The CLI, not
    pyttsx3: pyttsx3's espeak driver needs the legacy libespeak.so.1 which the
    'espeak-ng' apt package does not ship.) Text goes in via stdin so odd
    characters / leading dashes / long lines can't be read as options."""
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if not binary:
        raise VideoRenderingError("espeak-ng is not installed (apt-get install espeak-ng) - cannot narrate the video")

    voice = "hi" if _contains_devanagari(text) else "en"
    cmd = [binary, "-v", voice, "-s", str(NARRATION_WORDS_PER_MINUTE), "--stdin", "-w", out_path]
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise VideoRenderingError("Text-to-speech timed out") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise VideoRenderingError(f"Text-to-speech failed: {detail or exc}") from exc

    out = Path(out_path)
    if not out.exists() or out.stat().st_size == 0:
        raise VideoRenderingError("Text-to-speech produced no audio")


def _wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #
def _ffmpeg_binary() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # ImportError, or the wheel has no binary for this platform
        raise VideoRenderingError(
            "ffmpeg not found - install it (apt-get install ffmpeg) or `pip install imageio-ffmpeg`"
        ) from exc


def ffmpeg_available() -> bool:
    try:
        _ffmpeg_binary()
        return True
    except VideoRenderingError:
        return False


def _run_ffmpeg(args: list, timeout: int = 180) -> None:
    cmd = [_ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-y", *args]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise VideoRenderingError("ffmpeg timed out") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()[-400:]
        raise VideoRenderingError(f"ffmpeg failed: {detail or exc}") from exc


def _encode_segment(slide_path: str, audio_path, duration: float, out_path: str) -> None:
    """One scene -> one mp4 segment (still image for `duration` seconds + optional audio)."""
    args = ["-loop", "1", "-framerate", str(FPS), "-i", slide_path]
    if audio_path:
        args += ["-i", audio_path]
    args += ["-t", f"{duration:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
             "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS * 2)]
    if audio_path:
        # apad + -t: keep the slide up a moment after the voice ends
        args += ["-af", "apad", "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "1"]
    else:
        args += ["-an"]
    _run_ffmpeg(args + [out_path])


def _vtt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _write_vtt(path: Path, cues: list) -> None:
    """cues: [(start_seconds, end_seconds, text), ...]"""
    lines = ["WEBVTT", ""]
    for i, (start, end, text) in enumerate(cues, start=1):
        lines += [str(i), f"{_vtt_timestamp(start)} --> {_vtt_timestamp(end)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def artifact_paths(relative_mp4: str) -> list:
    """All files a render produced for a given 'videos/<id>.mp4' path."""
    base = relative_mp4[: -len(".mp4")] if relative_mp4.endswith(".mp4") else relative_mp4
    return [relative_mp4, base + ".vtt", base + ".jpg"]


def delete_rendered(relative_mp4: str, output_dir: str) -> None:
    """Removes a render's files (used when the DB write that would have
    referenced them fails, so they don't pile up as orphans)."""
    root = Path(output_dir).resolve()
    for rel in artifact_paths(relative_mp4):
        try:
            target = (root / rel).resolve()
            if root in target.parents and target.exists():
                target.unlink()
        except OSError:
            logger.warning("Could not delete rendered file %s", rel)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def render_video(chapter_name: str, script_body: str, output_dir: str) -> str:
    """Renders the script into a real .mp4 (+ .vtt captions + .jpg poster) under
    output_dir/videos/. Returns the mp4's relative path ('videos/<id>.mp4') for
    building a servable media_url. Raises VideoRenderingError on failure -
    callers should catch it and fall back to script-only content."""
    scenes = _parse_scene_details(script_body)
    if not scenes:
        raise VideoRenderingError("Could not extract any scenes from the script")

    if not _render_slots.acquire(timeout=getattr(settings, "RENDER_QUEUE_TIMEOUT_SECONDS", 900)):
        raise VideoRenderingError("Too many videos are rendering right now - try again shortly")
    try:
        return _render_locked(chapter_name, scenes, output_dir)
    finally:
        _render_slots.release()


def _render_locked(chapter_name: str, scenes: list, output_dir: str) -> str:
    videos_dir = Path(output_dir) / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    work_id = uuid.uuid4().hex[:12]
    work_dir = videos_dir / f"_tmp_{work_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = videos_dir / f"{work_id}.mp4"
    vtt_path = videos_dir / f"{work_id}.vtt"
    jpg_path = videos_dir / f"{work_id}.jpg"

    # Narration is optional: without espeak-ng (e.g. a Mac/Windows dev machine)
    # we still render the video - a silent slideshow. If espeak-ng IS installed
    # but errors, that is a real problem and is raised.
    narrate = narration_available()
    if not narrate:
        logger.warning("espeak-ng not found - rendering a SILENT video (install espeak-ng for narration)")

    try:
        segments, cues, clock = [], [], 0.0
        total = len(scenes)
        for i, scene in enumerate(scenes, start=1):
            text = scene["text"]
            slide_path = str(work_dir / f"slide_{i}.png")
            _make_slide_image(i, chapter_name, text, slide_path, total=total, label=scene["label"])
            if i == 1:
                from PIL import Image
                Image.open(slide_path).convert("RGB").save(jpg_path, quality=85)

            audio_path = None
            if narrate:
                audio_path = str(work_dir / f"narration_{i}.wav")
                _narrate(text, audio_path)
                duration = _wav_duration(audio_path) + 0.7   # small pad so the voice isn't cut off
            else:
                duration = _silent_duration(text)

            seg_path = str(work_dir / f"seg_{i}.mp4")
            _encode_segment(slide_path, audio_path, duration, seg_path)
            segments.append(seg_path)
            cues.append((clock, clock + duration, text))
            clock += duration

        list_file = work_dir / "segments.txt"
        list_file.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in segments), encoding="utf-8")
        _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy",
                     "-movflags", "+faststart", str(mp4_path)])
        _write_vtt(vtt_path, cues)
        return f"videos/{mp4_path.name}"

    except VideoRenderingError:
        _cleanup_outputs(mp4_path, vtt_path, jpg_path)
        raise
    except Exception as exc:
        logger.exception("Video rendering failed for chapter '%s'", chapter_name)
        _cleanup_outputs(mp4_path, vtt_path, jpg_path)
        raise VideoRenderingError(f"Video rendering failed: {exc}") from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)   # temp slides/audio/segments


def _cleanup_outputs(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
