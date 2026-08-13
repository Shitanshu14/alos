"""Week 1-2: signup, login, roles, dashboard."""
import pytest


class TestAuth:
    def test_signup_all_roles(self, client, make_user):
        for role in ("teacher", "parent", "student", "school"):
            user, _ = make_user(role)
            assert user["role"] == role

    def test_admin_role_rejected_at_public_signup(self, client):
        """Security regression test: admin must NEVER be self-registerable
        through the public signup endpoint - see app/create_admin.py for
        the correct (trusted-operator-only) way to create one."""
        r = client.post("/auth/signup", json={
            "full_name": "Wannabe Admin", "email": "wannabe_admin@test.com", "phone": "9555555555",
            "password": "Pass@123", "role": "admin",
        })
        assert r.status_code == 422

    def test_invalid_role_rejected(self, client):
        r = client.post("/auth/signup", json={
            "full_name": "X", "email": "badrole@test.com", "phone": "9111111111",
            "password": "Pass@123", "role": "user",
        })
        assert r.status_code == 422

    def test_duplicate_email_rejected(self, client, make_user):
        user, _ = make_user("teacher", email="dup@test.com")
        r = client.post("/auth/signup", json={
            "full_name": "Dup", "email": "dup@test.com", "phone": "9222222222",
            "password": "Pass@123", "role": "teacher",
        })
        assert r.status_code == 409

    def test_weak_password_rejected(self, client):
        r = client.post("/auth/signup", json={
            "full_name": "Weak", "email": "weak@test.com", "phone": "9333333333",
            "password": "weak", "role": "teacher",
        })
        assert r.status_code == 422

    def test_whitespace_only_name_rejected(self, client):
        """Regression test: signup previously accepted a name that was
        just whitespace (e.g. '   ')."""
        r = client.post("/auth/signup", json={
            "full_name": "   ", "email": "wsname@test.com", "phone": "9888888888",
            "password": "Pass@123", "role": "teacher",
        })
        assert r.status_code == 422

    def test_email_is_case_insensitive(self, client):
        """Regression test: 'User@Test.com' and 'user@test.com' were
        previously treated as different accounts, so someone could sign
        up twice with the same real email, and login could fail if the
        casing didn't exactly match what was stored at signup."""
        client.post("/auth/signup", json={
            "full_name": "Case Test", "email": "MixedCase@Test.com", "phone": "9777000111",
            "password": "Pass@123", "role": "teacher",
        })
        # Same email, different case - signup should be rejected as a duplicate
        r = client.post("/auth/signup", json={
            "full_name": "Dup", "email": "mixedcase@test.com", "phone": "9777000112",
            "password": "Pass@123", "role": "teacher",
        })
        assert r.status_code == 409

        # Login should work regardless of the casing used
        r = client.post("/auth/login", json={"email": "MIXEDCASE@TEST.COM", "password": "Pass@123"})
        assert r.status_code == 200

    def test_wrong_password_rejected(self, client, make_user):
        user, headers = make_user("teacher", email="wp@test.com")
        r = client.post("/auth/login", json={"email": "wp@test.com", "password": "WrongPass@1"})
        assert r.status_code == 401

    def test_no_token_rejected(self, client):
        r = client.get("/auth/me")
        assert r.status_code in (401, 403)

    def test_refresh_token_works(self, client):
        client.post("/auth/signup", json={
            "full_name": "R", "email": "refresh@test.com", "phone": "9444444444",
            "password": "Pass@123", "role": "teacher",
        })
        login = client.post("/auth/login", json={"email": "refresh@test.com", "password": "Pass@123"})
        refresh_token = login.json()["refresh_token"]
        r = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert r.status_code == 200
        assert "access_token" in r.json()


class TestDashboard:
    def test_teacher_dashboard(self, client, make_user):
        user, headers = make_user("teacher")
        r = client.get("/dashboard/", headers=headers)
        assert r.status_code == 200
        assert r.json()["role"] == "teacher"
        assert "ready_chapter_count" in r.json()

    def test_parent_dashboard_honest_placeholder(self, client, make_user):
        user, headers = make_user("parent")
        r = client.get("/dashboard/", headers=headers)
        assert r.status_code == 200
        assert r.json()["role"] == "parent"
