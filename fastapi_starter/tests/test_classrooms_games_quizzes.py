"""Week 12-16: classrooms/homework, games, quizzes, feedback."""
from conftest import wait_for_chapter_ready



class TestClassrooms:
    def test_create_enroll_and_access(self, client, make_user, sample_docx):
        teacher, teacher_h = make_user("teacher")
        student, student_h = make_user("student")
        student_id = client.get("/auth/me", headers=student_h).json()["id"]

        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=teacher_h,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "P"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, teacher_h, cid)

        classroom = client.post("/classrooms/", headers=teacher_h, json={
            "name": "Section A", "subject": "Science", "class_name": "Class 6",
        }).json()

        client.post(f"/classrooms/{classroom['id']}/enroll", headers=teacher_h, json={"student_user_id": student_id})
        client.post(f"/classrooms/{classroom['id']}/homework", headers=teacher_h, json={"title": "HW", "chapter_id": cid})

        content = client.get(f"/classrooms/{classroom['id']}/content", headers=student_h).json()
        assert len(content) >= 1

        # The homework link should also unlock the chapter itself, not just the game/quiz surfaces
        r = client.get(f"/chapters/{cid}/matches", headers=student_h)
        assert r.status_code == 200

    def test_non_teacher_cannot_create_classroom(self, client, make_user):
        student, headers = make_user("student")
        r = client.post("/classrooms/", headers=headers, json={"name": "X", "subject": "Y", "class_name": "Z"})
        assert r.status_code == 403

    def test_admin_has_oversight_access_to_any_classroom(self, client, make_user, sample_docx):
        """Regression test: admin was getting 403 on GET /classrooms/ and
        every classroom sub-resource unless they personally created the
        classroom - reported directly by a user testing as an admin."""
        teacher, teacher_h = make_user("teacher")
        admin, admin_h = make_user("admin")

        classroom = client.post("/classrooms/", headers=teacher_h, json={
            "name": "Section A", "subject": "Science", "class_name": "Class 6",
        }).json()

        assert client.get(f"/classrooms/{classroom['id']}/students", headers=admin_h).status_code == 200
        assert client.get(f"/classrooms/{classroom['id']}/homework", headers=admin_h).status_code == 200

        all_classrooms = client.get("/classrooms/", headers=admin_h).json()
        assert any(c["id"] == classroom["id"] for c in all_classrooms)

        # A different regular teacher must still be blocked - this fix
        # must not weaken normal cross-teacher isolation.
        other_teacher, other_h = make_user("teacher")
        assert client.get(f"/classrooms/{classroom['id']}/students", headers=other_h).status_code == 403

    def test_unenroll_student_and_delete_homework(self, client, make_user, sample_docx):
        """Regression test (QA Round 3): there was previously no way for
        a teacher to undo an enrollment mistake or cancel a wrong
        homework assignment - a real gap a teacher would hit constantly
        (wrong student enrolled, student transfers out, wrong chapter
        assigned by mistake)."""
        teacher, teacher_h = make_user("teacher")
        student, student_h = make_user("student")
        student_id = client.get("/auth/me", headers=student_h).json()["id"]

        classroom = client.post("/classrooms/", headers=teacher_h, json={
            "name": "A", "subject": "Science", "class_name": "Class 6",
        }).json()
        client.post(f"/classrooms/{classroom['id']}/enroll", headers=teacher_h, json={"student_user_id": student_id})
        assert len(client.get(f"/classrooms/{classroom['id']}/students", headers=teacher_h).json()) == 1

        r = client.delete(f"/classrooms/{classroom['id']}/students/{student_id}", headers=teacher_h)
        assert r.status_code == 200
        assert len(client.get(f"/classrooms/{classroom['id']}/students", headers=teacher_h).json()) == 0

        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=teacher_h,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "UnenrollTest"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, teacher_h, cid)

        hw = client.post(f"/classrooms/{classroom['id']}/homework", headers=teacher_h, json={
            "title": "Mistake", "chapter_id": cid,
        }).json()
        r = client.delete(f"/classrooms/{classroom['id']}/homework/{hw['id']}", headers=teacher_h)
        assert r.status_code == 200
        assert len(client.get(f"/classrooms/{classroom['id']}/homework", headers=teacher_h).json()) == 0

        r_404 = client.delete(f"/classrooms/{classroom['id']}/homework/999999", headers=teacher_h)
        assert r_404.status_code == 404


class TestGames:
    def test_concept_match_and_memory(self, client, make_user, sample_docx):
        teacher, headers = make_user("teacher")
        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "P"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, headers, cid)

        concept_match = client.get(f"/games/concept-match/{cid}", headers=headers).json()
        assert len(concept_match["pairs"]) > 0

        memory = client.get(f"/games/memory/{cid}", headers=headers).json()
        assert len(memory["cards"]) % 2 == 0  # always pairs
        concepts_seen = [c["concept"] for c in memory["cards"]]
        assert all(concepts_seen.count(c) == 2 for c in set(concepts_seen))  # each concept appears exactly twice


class TestQuizzes:
    def test_generate_submit_and_retry(self, client, make_user, sample_docx):
        teacher, headers = make_user("teacher")
        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "P"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, headers, cid)

        quiz = client.get(f"/quizzes/chapter/{cid}", headers=headers).json()
        assert len(quiz["questions"]) > 0
        n = len(quiz["questions"])

        result1 = client.post(f"/quizzes/{quiz['quiz_id']}/submit", headers=headers, json={"answers": [0] * n}).json()
        assert result1["attempt_number"] == 1

        # Unlimited retries - submit again using the revealed correct answers
        result2 = client.post(f"/quizzes/{quiz['quiz_id']}/submit", headers=headers,
                               json={"answers": result1["correct_indices"]}).json()
        assert result2["attempt_number"] == 2
        assert result2["score"] == n

        attempts = client.get(f"/quizzes/{quiz['quiz_id']}/attempts", headers=headers).json()
        assert len(attempts) == 2

    def test_wrong_answer_count_rejected(self, client, make_user, sample_docx):
        teacher, headers = make_user("teacher")
        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "P"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, headers, cid)
        quiz = client.get(f"/quizzes/chapter/{cid}", headers=headers).json()

        r = client.post(f"/quizzes/{quiz['quiz_id']}/submit", headers=headers, json={"answers": [0]})
        assert r.status_code == 422

    def test_concurrent_quiz_generation_no_crash(self, client, make_user, sample_docx):
        """Regression test: two+ requests hitting GET /quizzes/chapter/{id}
        at the same moment, before a quiz exists yet, used to race on
        creating the Quiz row (unique per chapter) - the loser crashed
        with an unhandled 500 IntegrityError instead of just returning
        the quiz the winner created."""
        import threading

        teacher, headers = make_user("teacher")
        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "P"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, headers, cid)

        results = []
        def hit_quiz():
            r = client.get(f"/quizzes/chapter/{cid}", headers=headers)
            results.append(r.status_code)

        threads = [threading.Thread(target=hit_quiz) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(code == 200 for code in results), f"got statuses: {results}"

    def test_out_of_range_answer_index_rejected(self, client, make_user, sample_docx):
        """Regression test: submitting an answer index outside the valid
        option range (e.g. 99 when there are only 4 options), or a
        negative index, used to be silently accepted and just scored as
        wrong instead of being rejected as malformed input."""
        teacher, headers = make_user("teacher")
        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "P"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, headers, cid)
        quiz = client.get(f"/quizzes/chapter/{cid}", headers=headers).json()
        n = len(quiz["questions"])

        r = client.post(f"/quizzes/{quiz['quiz_id']}/submit", headers=headers, json={"answers": [99] * n})
        assert r.status_code == 422

        r = client.post(f"/quizzes/{quiz['quiz_id']}/submit", headers=headers, json={"answers": [-1] * n})
        assert r.status_code == 422


class TestFeedback:
    def test_submit_and_admin_review(self, client, make_user):
        admin, admin_h = make_user("admin")
        student, student_h = make_user("student")

        fb = client.post("/feedback/", headers=student_h, json={
            "category": "bug", "message": "Something broke", "rating": 3,
        })
        assert fb.status_code == 201

        r = client.get("/feedback/", headers=student_h)
        assert r.status_code == 403  # not admin

        r = client.get("/feedback/", headers=admin_h)
        assert len(r.json()) >= 1

        fid = fb.json()["id"]
        r = client.patch(f"/feedback/{fid}/status", headers=admin_h, params={"new_status": "resolved"})
        assert r.json()["status"] == "resolved"


class TestHealthAndCaching:
    def test_health_check(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["database"] == "ok"

    def test_content_library_cache_invalidates_on_write(self, client, make_user):
        admin, headers = make_user("admin")
        client.post("/content-library/seed", headers=headers)

        before = len(client.get("/content-library/", headers=headers, params={"limit": 200}).json())
        client.post("/content-library/", headers=headers, json={
            "title": "New", "content_type": "story", "subject": "Science", "class_name": "Class 6",
        })
        after = len(client.get("/content-library/", headers=headers, params={"limit": 200}).json())
        assert after == before + 1
