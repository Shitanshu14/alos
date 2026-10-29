"""Week 3-8: chapter upload through the full processing pipeline."""
from conftest import wait_for_chapter_ready


def test_hindi_chapter_name_renders_readable_text_in_video():
    """Regression test: Hindi/Devanagari text used to render as empty 'tofu'
    boxes because DejaVu Sans has no Devanagari glyphs; the fix picks Noto Sans
    Devanagari per text run.

    This test used to be ineffective: it measured a region that also contained
    the English word 'Scene', whose glyphs alone pushed the colour count over
    the threshold - so it passed even when every Hindi character was a tofu box.
    It now looks ONLY at the Hindi chapter name in the header band (measured:
    ~220 distinct colours with a real font vs 3 with tofu boxes)."""
    import pytest
    from pathlib import Path
    from app.services import video_rendering_service as v
    import numpy as np
    from PIL import Image
    import tempfile

    if not any(Path(p).exists() for p in v.DEVANAGARI_FONT_CANDIDATES):
        pytest.skip("No Devanagari font installed here (apt-get install fonts-noto-core)")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = f"{tmpdir}/slide.png"
        v._make_slide_image(1, "प्रकाश संश्लेषण", "पौधे सूर्य के प्रकाश से भोजन बनाते हैं।", out_path, total=2)

        arr = np.array(Image.open(out_path))
        title_area = arr[0:108, 0:700]          # header band, Hindi chapter name only
        unique_colors = len(np.unique(title_area.reshape(-1, title_area.shape[-1]), axis=0))
        assert unique_colors > 100, f"Hindi text may not be rendering correctly - only {unique_colors} unique colors found"


def test_video_is_still_rendered_without_narration(monkeypatch, tmp_path):
    """No espeak-ng (e.g. a Mac/Windows dev machine) must NOT mean 'no video':
    a silent slideshow is rendered instead of giving up."""
    from app.services import video_rendering_service as v

    monkeypatch.setattr(v, "narration_available", lambda: False)
    script = (
        "VIDEO SCRIPT - Test\n\n"
        "Scene 1 (Hook): Ask a question about the topic.\n"
        "Scene 2 (Core idea): Explain the main idea step by step.\n"
    )
    rel = v.render_video("Test", script, str(tmp_path))
    out = tmp_path / rel
    assert out.exists() and out.stat().st_size > 5_000
    assert out.read_bytes()[4:8] == b"ftyp"
    assert b"mp4a" not in out.read_bytes(), "no narration -> the file should have no audio track"


def test_seed_placeholder_video_is_not_a_valid_match():
    """Regression: seeded video items point at a fake CDN (cdn.alos.app), but
    validate_match only checked the URL was non-empty - so it passed, no video
    was ever rendered, and chapters ended up with a dead video link."""
    from app import models
    from app.services.matching_service import validate_match

    tag = models.ContentTag(name="photosynthesis")
    fake = models.ContentItem(
        title="x", content_type="video", subject="Science", class_name="Class 6",
        media_url="https://cdn.alos.app/videos/photosynthesis.mp4", is_seed_placeholder=True, tags=[tag],
    )
    ok, _ = validate_match(fake, 1.0)
    assert ok is False

    real = models.ContentItem(
        title="y", content_type="video", subject="Science", class_name="Class 6",
        media_url="/media/videos/abc.mp4", is_seed_placeholder=False, tags=[tag],
    )
    ok, _ = validate_match(real, 1.0)
    assert ok is True


def _upload_and_wait(client, headers, docx_path, chapter_name="Photosynthesis",
                      subject="Science", class_name="Class 6", wait=45):
    with open(docx_path, "rb") as f:
        r = client.post(
            "/chapters/upload", headers=headers,
            data={"class_name": class_name, "subject": subject, "chapter_name": chapter_name},
            files={"file": ("chapter.docx", f, "application/octet-stream")},
        )
    assert r.status_code == 201
    chapter_id = r.json()["id"]
    wait_for_chapter_ready(client, headers, chapter_id, timeout=wait)
    return chapter_id


class TestChapterUpload:
    def test_rate_limit_key_prefers_user_over_ip(self, client, make_user):
        """Regression test (QA Round 3): chapter-upload rate limiting used
        to be purely per-IP. ALOS's real users are schools where many
        teachers/students share one IP behind their network's NAT -
        confirmed directly (via a manual concurrency script, outside this
        suite) that one teacher exhausting their hourly quota blocked
        every OTHER teacher's account on the same network too, even ones
        who'd never uploaded anything. Fixed by keying authenticated
        endpoints per-user instead of per-IP (see rate_limiter.py's
        user_or_ip_key). This tests the key function directly, since the
        test suite relaxes the actual rate limit values (see conftest.py)
        and so can't exercise the real 429 boundary end-to-end."""
        from starlette.requests import Request
        from app.rate_limiter import user_or_ip_key

        teacher, headers = make_user("teacher")
        token = headers["Authorization"].split(" ")[1]

        def make_request(auth_header: str) -> Request:
            scope = {
                "type": "http",
                "headers": [(b"authorization", auth_header.encode())] if auth_header else [],
                "client": ("127.0.0.1", 12345),
            }
            return Request(scope)

        key_with_token = user_or_ip_key(make_request(f"Bearer {token}"))
        assert key_with_token.startswith("user:"), f"expected a per-user key, got {key_with_token}"

        key_without_token = user_or_ip_key(make_request(""))
        assert key_without_token.startswith("ip:"), f"expected a per-IP key for unauthenticated requests, got {key_without_token}"

        key_with_garbage_token = user_or_ip_key(make_request("Bearer not-a-real-token"))
        assert key_with_garbage_token.startswith("ip:"), "an invalid token should fall back to IP-based limiting, not crash"

    def test_homework_due_date_in_past_rejected(self, client, make_user, sample_docx):
        """Regression test (QA Round 3): homework due_date accepted any
        date, including years in the past - a teacher could accidentally
        assign homework that's already 'overdue' the moment students see
        it (e.g. a date-picker bug or wrong year selected)."""
        teacher, teacher_h = make_user("teacher")
        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=teacher_h,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "DueDate"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, teacher_h, cid)

        classroom = client.post("/classrooms/", headers=teacher_h, json={
            "name": "A", "subject": "Science", "class_name": "Class 6",
        }).json()

        r = client.post(f"/classrooms/{classroom['id']}/homework", headers=teacher_h, json={
            "title": "HW", "chapter_id": cid, "due_date": "2020-01-01T00:00:00",
        })
        assert r.status_code == 422

    def test_duplicate_chapter_upload_rejected(self, client, make_user, sample_docx):
        """Regression test (QA Round 3): accidentally double-clicking
        'Upload' (common on slow connections) created two identical
        chapters, each running its own full background pipeline."""
        teacher, headers = make_user("teacher")

        with open(sample_docx, "rb") as f:
            r1 = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "DupCheck"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        assert r1.status_code == 201

        with open(sample_docx, "rb") as f:
            r2 = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "DupCheck"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        assert r2.status_code == 409

        # A genuinely different chapter from the same teacher must still work
        with open(sample_docx, "rb") as f:
            r3 = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "NotADuplicate"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        assert r3.status_code == 201

    def test_upload_pdf_and_docx_only(self, client, make_user, sample_docx):
        user, headers = make_user("teacher")
        r = client.post(
            "/chapters/upload", headers=headers,
            data={"class_name": "Class 6", "subject": "Science", "chapter_name": "X"},
            files={"file": ("bad.txt", b"hello", "text/plain")},
        )
        assert r.status_code == 422

    def test_any_role_can_upload_a_chapter(self, client, make_user, sample_docx):
        """There is no role restriction on upload: teacher, parent, school,
        student, user and admin can all upload. (Previously only
        teacher/school could - parents got a 403.)"""
        for i, role in enumerate(("teacher", "parent", "school", "student", "user", "admin")):
            user, headers = make_user(role)
            with open(sample_docx, "rb") as f:
                r = client.post(
                    "/chapters/upload", headers=headers,
                    data={"class_name": "Class 6", "subject": "Science", "chapter_name": f"Role check {role}"},
                    files={"file": ("chapter.docx", f, "application/octet-stream")},
                )
            assert r.status_code == 201, f"{role} should be able to upload, got {r.status_code}: {r.text}"
            # the uploader owns it: it shows up in their own chapter list
            mine = [c["id"] for c in client.get("/chapters/", headers=headers).json()]
            assert r.json()["id"] in mine

    def test_upload_still_requires_login(self, client, sample_docx):
        with open(sample_docx, "rb") as f:
            r = client.post(
                "/chapters/upload",
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "Anon"},
                files={"file": ("chapter.docx", f, "application/octet-stream")},
            )
        assert r.status_code in (401, 403)

    def test_parent_can_upload_a_pdf_and_it_is_fully_processed(self, client, make_user, unrelated_pdf):
        """End to end with a real PDF uploaded by a PARENT: text is
        extracted, the brief is created, and all 4 content types resolve."""
        parent, headers = make_user("parent")
        with open(unrelated_pdf, "rb") as f:
            r = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 12", "subject": "Industrial Chemistry", "chapter_name": "Zeolite Catalysis PDF"},
                files={"file": ("chapter.pdf", f, "application/pdf")},
            )
        assert r.status_code == 201, r.text
        assert r.json()["file_type"] == "pdf"
        cid = r.json()["id"]

        assert wait_for_chapter_ready(client, headers, cid, timeout=60) == "ready"
        matches = client.get(f"/chapters/{cid}/matches", headers=headers).json()
        assert len(matches) == 4
        assert all(m["match_status"] in ("matched", "generated") for m in matches)

    def test_upload_and_process(self, client, make_user, sample_docx):
        user, headers = make_user("teacher")
        cid = _upload_and_wait(client, headers, sample_docx)
        status = client.get(f"/chapters/{cid}/status", headers=headers).json()
        assert status["status"] == "ready"

    def test_non_owner_cannot_access(self, client, make_user, sample_docx):
        owner, owner_h = make_user("teacher")
        other, other_h = make_user("teacher")
        cid = _upload_and_wait(client, owner_h, sample_docx)
        r = client.get(f"/chapters/{cid}/status", headers=other_h)
        assert r.status_code == 403

    def test_admin_has_oversight_access_to_any_chapter(self, client, make_user, sample_docx):
        """Regression test: admin accounts were getting 403 on every
        chapter/classroom endpoint unless they personally created the
        resource - reported directly by a user testing the API as an
        admin and finding they had no access to chapters, content, or
        classrooms at all. Admin should have platform-wide oversight."""
        teacher, teacher_h = make_user("teacher")
        admin, admin_h = make_user("admin")
        cid = _upload_and_wait(client, teacher_h, sample_docx)

        assert client.get(f"/chapters/{cid}/status", headers=admin_h).status_code == 200
        assert client.get(f"/chapters/{cid}/brief", headers=admin_h).status_code == 200
        assert client.get(f"/chapters/{cid}/matches", headers=admin_h).status_code == 200

        all_chapters = client.get("/chapters/", headers=admin_h).json()
        assert any(ch["id"] == cid for ch in all_chapters), "admin's chapter list should include chapters uploaded by other teachers"

        # A regular (non-admin) teacher must still be blocked from another
        # teacher's chapter - this fix must not weaken normal isolation.
        other_teacher, other_h = make_user("teacher")
        assert client.get(f"/chapters/{cid}/status", headers=other_h).status_code == 403


class TestChapterBrief:
    def test_brief_has_key_concepts(self, client, make_user, sample_docx):
        user, headers = make_user("teacher")
        cid = _upload_and_wait(client, headers, sample_docx)
        brief = client.get(f"/chapters/{cid}/brief", headers=headers).json()
        assert len(brief["key_concepts"]) > 0
        assert brief["summary"]


class TestMatchingAndGeneration:
    def test_matches_against_seeded_library(self, client, make_user, sample_docx):
        admin, admin_h = make_user("admin")
        teacher, teacher_h = make_user("teacher")
        client.post("/content-library/seed", headers=admin_h)

        cid = _upload_and_wait(client, teacher_h, sample_docx)
        matches = client.get(f"/chapters/{cid}/matches", headers=teacher_h).json()

        assert len(matches) == 4
        assert all(m["match_status"] in ("matched", "generated") for m in matches)

    def test_seeded_topic_gets_a_real_playable_video(self, client, make_user, sample_docx):
        """THE regression test for 'videos are not being created': with the
        library seeded and a chapter on a seeded topic (photosynthesis), the
        video used to be a 'matched' seed placeholder whose URL is a fake CDN
        link - nothing was ever rendered. It must be a real, playable file."""
        admin, admin_h = make_user("admin")
        teacher, teacher_h = make_user("teacher")
        client.post("/content-library/seed", headers=admin_h)

        cid = _upload_and_wait(client, teacher_h, sample_docx, chapter_name="Photosynthesis Seeded Video")
        matches = client.get(f"/chapters/{cid}/matches", headers=teacher_h).json()
        video = next(m for m in matches if m["content_type"] == "video")

        media_url = video["content_item"]["media_url"]
        assert media_url and media_url.startswith("/media/"), f"video is not a real file: {media_url}"
        assert "cdn.alos.app" not in media_url
        assert video["content_item"]["is_seed_placeholder"] is False

        r = client.get(media_url)
        assert r.status_code == 200
        assert len(r.content) > 10_000
        assert r.content[4:8] == b"ftyp"

    def test_generation_for_unseeded_subject(self, client, make_user, unrelated_docx):
        teacher, teacher_h = make_user("teacher")
        # unrelated_docx has content genuinely absent from the seed library's
        # 27 topics, so this forces generation for every content type
        # regardless of what other tests have seeded into the shared DB.
        cid = _upload_and_wait(
            client, teacher_h, unrelated_docx,
            chapter_name="Quantum Entanglement",
            subject="Advanced Physics Research", class_name="Class 12", wait=45,
        )
        matches = client.get(f"/chapters/{cid}/matches", headers=teacher_h).json()
        assert all(m["match_status"] == "generated" for m in matches)

    def test_generated_video_is_a_real_playable_file(self, client, make_user, video_docx):
        """Regression test: video used to silently fall back to 'script only'
        on every host (pyttsx3 needed libespeak.so.1, which the espeak-ng
        package doesn't ship) - and this test still passed, because it only
        checked the video IF one existed. Now a real, playable video MUST be
        produced (narrated if espeak-ng is installed, silent otherwise).

        video_docx (a topic absent from the seed library, and private to
        this test) is used instead of sample_docx, so the video is
        guaranteed to be generated rather than matched."""
        teacher, teacher_h = make_user("teacher")
        cid = _upload_and_wait(
            client, teacher_h, video_docx,
            chapter_name="Byzantine Fault Tolerance",
            subject="Distributed Computing", class_name="Class 12", wait=90,
        )
        matches = client.get(f"/chapters/{cid}/matches", headers=teacher_h).json()
        video = next(m for m in matches if m["content_type"] == "video")

        assert video["match_status"] == "generated"
        media_url = video["content_item"]["media_url"]
        assert media_url, f"video was NOT rendered - fell back to script only. Reason: {video['quality_notes']}"

        r = client.get(media_url)
        assert r.status_code == 200
        assert len(r.content) > 10_000
        assert r.content[4:8] == b"ftyp", "file is not an MP4 container"

        from app.services.video_rendering_service import narration_available
        if narration_available():
            assert b"mp4a" in r.content, "espeak-ng is installed but the video has no narration audio"
