"""Week 8-11: notifications, progress tracking, parent-child, sharing."""
from conftest import wait_for_chapter_ready



class TestNotificationsAndProgress:
    def test_notification_on_chapter_ready(self, client, make_user, sample_docx):
        teacher, headers = make_user("teacher")
        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "P"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, headers, cid)
        notifs = client.get("/notifications/", headers=headers).json()
        assert any("ready" in n["title"].lower() for n in notifs)

    def test_concurrent_progress_access_no_duplicate_rows(self, client, make_user):
        """Regression test (Testing 2.0): concurrent first-time access to
        the same content item used to silently create duplicate
        ContentProgress rows (confirmed with 10 concurrent requests -> 2
        rows instead of 1). No crash, so it went unnoticed - a real
        silent data-corruption bug: whichever duplicate a later query
        happened to pick could show stale/wrong progress."""
        import threading

        admin, admin_h = make_user("admin")
        teacher, headers = make_user("teacher")
        client.post("/content-library/seed", headers=admin_h)
        item_id = client.get("/content-library/", headers=headers, params={"limit": 1}).json()[0]["id"]

        results = []
        def hit_access():
            r = client.post(f"/progress/access/{item_id}", headers=headers)
            results.append(r.status_code)

        threads = [threading.Thread(target=hit_access) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(code == 200 for code in results)

        history = client.get("/progress/history", headers=headers).json()
        matching_rows = [h for h in history if h["content_item_id"] == item_id]
        assert len(matching_rows) == 1, f"expected exactly 1 progress row, found {len(matching_rows)}"

    def test_progress_access_and_resume(self, client, make_user):
        admin, admin_h = make_user("admin")
        teacher, headers = make_user("teacher")
        client.post("/content-library/seed", headers=admin_h)
        item_id = client.get("/content-library/", headers=headers, params={"limit": 1}).json()[0]["id"]

        r = client.post(f"/progress/access/{item_id}", headers=headers)
        assert r.json()["status"] == "in_progress"

        client.patch(f"/progress/{item_id}", headers=headers, json={"progress_percent": 60, "last_position_seconds": 90})
        r2 = client.post(f"/progress/access/{item_id}", headers=headers)
        assert r2.json()["progress_percent"] == 60
        assert r2.json()["last_position_seconds"] == 90

    def test_completed_chapters_and_history(self, client, make_user, sample_docx):
        admin, admin_h = make_user("admin")
        teacher, headers = make_user("teacher")
        client.post("/content-library/seed", headers=admin_h)

        with open(sample_docx, "rb") as f:
            up = client.post(
                "/chapters/upload", headers=headers,
                data={"class_name": "Class 6", "subject": "Science", "chapter_name": "P"},
                files={"file": ("c.docx", f, "application/octet-stream")},
            )
        cid = up.json()["id"]
        wait_for_chapter_ready(client, headers, cid)
        matches = client.get(f"/chapters/{cid}/matches", headers=headers).json()
        item_id = matches[0]["content_item"]["id"]

        client.post(f"/progress/access/{item_id}", headers=headers, params={"chapter_id": cid})
        client.patch(f"/progress/{item_id}", headers=headers, json={"status": "completed"})

        completed = client.get("/progress/completed-chapters", headers=headers).json()
        assert len(completed) == 1
        assert completed[0]["chapter_id"] == cid

        history = client.get("/progress/history", headers=headers).json()
        assert len(history) >= 1


class TestParentChild:
    def test_add_child_and_set_class(self, client, make_user):
        parent, headers = make_user("parent")
        child = client.post("/children/", headers=headers, json={"name": "Aarav", "class_name": "Class 4"}).json()

        r = client.patch(f"/children/{child['id']}/class", headers=headers, json={"class_name": "Class 5"})
        assert r.json()["class_name"] == "Class 5"

    def test_non_parent_cannot_add_child(self, client, make_user):
        teacher, headers = make_user("teacher")
        r = client.post("/children/", headers=headers, json={"name": "X"})
        assert r.status_code == 403

    def test_progress_report_isolated_per_parent(self, client, make_user):
        parent1, h1 = make_user("parent")
        parent2, h2 = make_user("parent")
        child = client.post("/children/", headers=h1, json={"name": "Kid"}).json()

        r = client.get(f"/children/{child['id']}/progress", headers=h2)
        assert r.status_code == 403


class TestSharing:
    def test_public_share_link(self, client, make_user):
        admin, admin_h = make_user("admin")
        teacher, headers = make_user("teacher")
        client.post("/content-library/seed", headers=admin_h)
        item_id = client.get("/content-library/", headers=headers, params={"limit": 1}).json()[0]["id"]

        link = client.post(f"/content-library/{item_id}/share", headers=headers, json={}).json()
        # No auth header - proving it's genuinely public
        r = client.get(f"/share/{link['token']}")
        assert r.status_code == 200
        assert "title" in r.json()

    def test_classroom_share_notifies_students(self, client, make_user):
        admin, admin_h = make_user("admin")
        teacher, headers = make_user("teacher")
        student, student_h = make_user("student")
        student_id = client.get("/auth/me", headers=student_h).json()["id"]
        client.post("/content-library/seed", headers=admin_h)
        item_id = client.get("/content-library/", headers=headers, params={"limit": 1}).json()[0]["id"]

        r = client.post(f"/content-library/{item_id}/share-classroom", headers=headers,
                         json={"student_user_ids": [student_id]})
        assert r.status_code == 200

        shared = client.get("/shared-with-me", headers=student_h).json()
        assert len(shared) == 1
