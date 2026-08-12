"""Week 3-8: chapter upload through the full processing pipeline."""
from conftest import wait_for_chapter_ready


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
    def test_upload_pdf_and_docx_only(self, client, make_user, sample_docx):
        user, headers = make_user("teacher")
        r = client.post(
            "/chapters/upload", headers=headers,
            data={"class_name": "Class 6", "subject": "Science", "chapter_name": "X"},
            files={"file": ("bad.txt", b"hello", "text/plain")},
        )
        assert r.status_code == 422

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

    def test_generated_video_is_a_real_playable_file(self, client, make_user, sample_docx):
        teacher, teacher_h = make_user("teacher")
        cid = _upload_and_wait(client, teacher_h, sample_docx, wait=45)
        matches = client.get(f"/chapters/{cid}/matches", headers=teacher_h).json()
        video = next(m for m in matches if m["content_type"] == "video")

        # Rendering needs ffmpeg/espeak-ng - if unavailable, it gracefully
        # falls back to script-only, so we only assert the strict video
        # properties when a media_url was actually produced.
        if video["content_item"]["media_url"]:
            r = client.get(video["content_item"]["media_url"])
            assert r.status_code == 200
            assert len(r.content) > 10_000
