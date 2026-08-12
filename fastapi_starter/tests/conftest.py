"""
Shared pytest fixtures for the ALOS test suite.

Every test gets a fresh, isolated SQLite database (a temp file, deleted
after the test session) and a FastAPI TestClient - so tests never
interfere with each other or with a real dev database.
"""
import os
import sys
import tempfile

import pytest

# Force a throwaway SQLite DB + a dummy secret key before the app (and its
# settings/engine) get imported anywhere.
_tmp_db_fd, _tmp_db_path = tempfile.mkstemp(suffix=".db")
os.close(_tmp_db_fd)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_db_path}")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("GENERATED_MEDIA_DIR", tempfile.mkdtemp())
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp())
# The real app limits login to 5/minute per IP (brute-force protection).
# The test suite legitimately logs in far more than that (many users,
# many tests) from a single "IP" (the TestClient), so relax it here only -
# production still uses the real 5/minute default from config.py.
os.environ.setdefault("LOGIN_RATE_LIMIT", "1000/minute")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user(client):
    """Signs up + logs in a user, returns (user_dict, auth_headers).
    Uses a random suffix (not a per-test counter) for the email/phone,
    since the test DB persists across the whole session - a counter that
    resets each test would collide across different test functions."""
    import uuid

    def _make(role: str, email: str = None):
        suffix = uuid.uuid4().hex[:10]
        email = email or f"{role}_{suffix}@test.com"
        phone = f"9{suffix[:9]}"
        client.post("/auth/signup", json={
            "full_name": f"{role.title()} {suffix[:4]}",
            "email": email, "phone": phone, "password": "Pass@123", "role": role,
        })
        login = client.post("/auth/login", json={"email": email, "password": "Pass@123"})
        token = login.json()["access_token"]
        user = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
        return user, {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture
def sample_docx(tmp_path):
    """A small but realistic chapter DOCX with enough repeated content
    for the local NLP extractor to find real key concepts."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(
        "Photosynthesis is the process by which green plants make their own food "
        "using sunlight. Photosynthesis occurs mainly in the leaves of plants."
    )
    doc.add_paragraph(
        "During photosynthesis, plants take in carbon dioxide from the air. "
        "Water is absorbed from the soil through the roots."
    )
    doc.add_paragraph(
        "The glucose produced during photosynthesis is used by the plant as food. "
        "Oxygen produced during photosynthesis is released into the atmosphere."
    )
    path = tmp_path / "chapter.docx"
    doc.save(str(path))
    return str(path)


@pytest.fixture
def unrelated_docx(tmp_path):
    """Content with concepts genuinely absent from the seed library (27
    topics across Science/Math/English/Social Studies/Hindi) - used to
    test the no-match -> generation path without accidentally matching
    via concept overlap the way reusing sample_docx with a fake subject
    label would (the seed matcher scores on concept overlap, not just
    subject/class, so a photosynthesis chapter mislabeled as another
    subject can still legitimately match a photosynthesis library item)."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(
        "Quantum entanglement is a phenomenon studied in advanced particle physics research. "
        "Entangled particles show correlated behavior regardless of the distance between them."
    )
    doc.add_paragraph(
        "Researchers study superposition and wavefunction collapse using specialized laboratory "
        "equipment such as cryogenic chambers and photon detectors."
    )
    doc.add_paragraph(
        "Quantum entanglement has potential applications in cryptography and quantum computing, "
        "an active area of ongoing scientific research."
    )
    path = tmp_path / "unrelated_chapter.docx"
    doc.save(str(path))
    return str(path)


def wait_for_chapter_ready(client, headers, chapter_id, timeout=45):
    """Polls /chapters/{id}/status instead of a fixed sleep - background
    processing time varies (video rendering in particular can take a
    while under load), so a fixed wait is flaky. Raises if it never
    reaches a terminal state within timeout."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        r = client.get(f"/chapters/{chapter_id}/status", headers=headers)
        current_status = r.json().get("status")
        if current_status in ("ready", "failed"):
            return current_status
        _time.sleep(1)
    raise TimeoutError(f"Chapter {chapter_id} did not finish processing within {timeout}s")
