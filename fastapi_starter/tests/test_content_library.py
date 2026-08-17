"""Week 5: content library CRUD, tagging, search, seeding."""


class TestContentLibrary:
    def test_seed_inserts_100_plus_items(self, client, make_user):
        admin, headers = make_user("admin")
        client.post("/content-library/seed", headers=headers)

        items = client.get("/content-library/", headers=headers, params={"limit": 200}).json()
        assert len(items) >= 100

    def test_seed_is_idempotent(self, client, make_user):
        admin, headers = make_user("admin")
        client.post("/content-library/seed", headers=headers)
        r2 = client.post("/content-library/seed", headers=headers)
        assert "already has" in r2.json()["message"]

    def test_search_wildcards_treated_literally(self, client, make_user):
        """Regression test (QA Round 3): searching for a literal '%' or
        '_' flooded results with nearly the whole library, because these
        are SQL LIKE wildcards and weren't being escaped."""
        admin, headers = make_user("admin")
        client.post("/content-library/seed", headers=headers)

        r_percent = client.get("/content-library/", headers=headers, params={"search": "%"})
        r_underscore = client.get("/content-library/", headers=headers, params={"search": "_"})
        assert len(r_percent.json()) == 0
        assert len(r_underscore.json()) == 0

        # Normal search must still work
        r_normal = client.get("/content-library/", headers=headers, params={"search": "Photosynthesis"})
        assert len(r_normal.json()) > 0

    def test_only_admin_can_create(self, client, make_user):
        admin, admin_h = make_user("admin")
        teacher, teacher_h = make_user("teacher")

        r = client.post("/content-library/", headers=teacher_h, json={
            "title": "X", "content_type": "story", "subject": "Science", "class_name": "Class 6",
        })
        assert r.status_code == 403

        r = client.post("/content-library/", headers=admin_h, json={
            "title": "X", "content_type": "story", "subject": "Science", "class_name": "Class 6",
            "tags": ["testing"],
        })
        assert r.status_code == 201

    def test_search_by_tag_and_subject(self, client, make_user):
        admin, headers = make_user("admin")
        client.post("/content-library/seed", headers=headers)

        r = client.get("/content-library/", headers=headers, params={"subject": "Science", "tag": "photosynthesis"})
        assert len(r.json()) > 0
        assert all(i["subject"] == "Science" for i in r.json())

    def test_update_and_delete(self, client, make_user):
        admin, headers = make_user("admin")
        created = client.post("/content-library/", headers=headers, json={
            "title": "Temp", "content_type": "poem", "subject": "Math", "class_name": "Class 4",
        }).json()

        updated = client.put(f"/content-library/{created['id']}", headers=headers, json={"title": "Updated"})
        assert updated.json()["title"] == "Updated"

        deleted = client.delete(f"/content-library/{created['id']}", headers=headers)
        assert deleted.status_code == 200
