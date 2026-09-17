import os
import uuid
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import SessionLocal, engine, Base
from backend import models, security, exam_engine, config
from backend.services.ingestion import parse_docx_questions, parse_csv_questions
from backend.seed import seed


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    seed(drop_tables=False)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)


def test_enum_native_compatibility():
    """Verify Task 17: native_enum=False on SQLAlchemy Enum definitions."""
    assert models.User.role.type.native_enum is False
    assert models.ExamSession.status.type.native_enum is False
    assert models.Question.question_type.type.native_enum is False


def test_production_secret_guard(monkeypatch):
    """Verify Task 15: Production environment guards against default SECRET_KEY."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("EXAM_SECRET_KEY", "CHANGE_THIS_BEFORE_ANY_REAL_USE_1a2b3c4d5e")
    
    with pytest.raises(RuntimeError, match="FATAL: Cannot use default SECRET_KEY in production"):
        import importlib
        importlib.reload(config)
    
    monkeypatch.setenv("ENV", "development")
    import importlib
    importlib.reload(config)


def test_docx_answer_key_ingestion():
    """Verify Task 1: Table-based and paragraph-based answer key ingestion."""
    # 1. Test CS docx (Table-based answer key)
    cs_path = "COMPUTER SCIENCE ENTRANCE TEST QUESTIONS.docx"
    if os.path.exists(cs_path):
        with open(cs_path, "rb") as f:
            cs_qs = parse_docx_questions(f.read(), "Computer Science & Engineering", phase=2)
        assert len(cs_qs) == 50
        correct_opts = [q.correct_option for q in cs_qs]
        assert any(c != 0 for c in correct_opts), "Options should not all be 0!"
        assert cs_qs[0].correct_option == 1
        assert cs_qs[1].correct_option == 0

    # 2. Test RM docx (Paragraph-based answer key)
    rm_path = "DSU_Research_Methodology_Set_II.docx"
    if os.path.exists(rm_path):
        with open(rm_path, "rb") as f:
            rm_qs = parse_docx_questions(f.read(), "Research Methodology", phase=1)
        assert len(rm_qs) == 50
        assert rm_qs[0].correct_option == 1
        assert rm_qs[1].correct_option == 1
        assert rm_qs[2].correct_option == 0


def test_domain_validation():
    """Verify Task 6: Domain validation rejects unconfigured domains."""
    with pytest.raises(ValueError, match="not valid"):
        parse_docx_questions(b"", "Invalid Fake Domain", phase=1)

    with pytest.raises(ValueError, match="not valid"):
        parse_csv_questions(b"domain,question_type,question_text\nInvalid,mcq,test\n", "Invalid", phase=1)


def test_seed_preserves_db_and_creates_candidate1(db):
    """Verify Task 2: Seeding creates candidate1 and preserves records."""
    c1 = db.query(models.User).filter(models.User.email == "candidate1@examportal.edu").first()
    assert c1 is not None
    assert c1.role == models.UserRole.candidate


def test_admin_ws_token_revocation(db):
    """Verify Task 14: decode_token_for_ws verifies is_active and token_version."""
    admin = db.query(models.User).filter(models.User.email == "admin@examportal.edu").first()
    assert admin is not None

    token = security.create_access_token(admin)
    decoded_user = security.decode_token_for_ws(token, db)
    assert decoded_user.id == admin.id

    # Bump token version to revoke
    admin.token_version += 1
    db.commit()

    # Now old token should be rejected
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        security.decode_token_for_ws(token, db)
    assert exc_info.value.status_code == 401


def test_strike_limit_and_disqualification(client, db):
    """Verify Task 8: 3 strikes triggers disqualification and revokes candidate token."""
    email = f"strike_tester_{uuid.uuid4().hex[:6]}@examportal.edu"
    user = models.User(
        email=email,
        full_name="Strike Tester",
        hashed_password=security.hash_password("Pass@1234"),
        role=models.UserRole.candidate,
        must_reset_password=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Start session
    session = exam_engine.start_session(db, user, dev=True)
    assert session.status == models.SessionStatus.in_progress

    token = security.create_access_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    # Strike 1
    r1 = client.post("/exam/violation", json={"violation_type": "tab_switch"}, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["action"] == "warn"
    assert r1.json()["strikes"] == 1

    # Strike 2
    r2 = client.post("/exam/violation", json={"violation_type": "copy_attempt"}, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["action"] == "warn"
    assert r2.json()["strikes"] == 2

    # Strike 3 -> Disqualification
    r3 = client.post("/exam/violation", json={"violation_type": "right_click"}, headers=headers)
    assert r3.status_code == 200
    assert r3.json()["action"] == "disqualify"
    assert r3.json()["strikes"] == 3

    # Token should now be revoked on any endpoint
    r_me = client.get("/auth/me", headers=headers)
    assert r_me.status_code == 401


def test_admin_manual_disqualification(client, db):
    """Verify Task 8: POST /admin/sessions/{id}/disqualify."""
    email = f"admin_disq_tester_{uuid.uuid4().hex[:6]}@examportal.edu"
    user = models.User(
        email=email,
        full_name="Admin Disq Tester",
        hashed_password=security.hash_password("Pass@1234"),
        role=models.UserRole.candidate,
        must_reset_password=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    session = exam_engine.start_session(db, user, dev=True)
    c_token = security.create_access_token(user)

    admin = db.query(models.User).filter(models.User.role == models.UserRole.admin).first()
    a_token = security.create_access_token(admin)
    a_headers = {"Authorization": f"Bearer {a_token}"}

    # Admin disqualifies candidate
    r = client.post(f"/admin/sessions/{session.id}/disqualify", headers=a_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "disqualified"

    db.refresh(session)
    assert session.status == models.SessionStatus.disqualified

    # Candidate token revoked
    r_cand = client.get("/auth/me", headers={"Authorization": f"Bearer {c_token}"})
    assert r_cand.status_code == 401


def test_intermission_auto_domain_expiry(db):
    """Verify Task 3: Intermission expiration auto-assigns domain and moves to Phase 3."""
    email = f"intermission_tester_{uuid.uuid4().hex[:6]}@examportal.edu"
    user = models.User(
        email=email,
        full_name="Intermission Tester",
        hashed_password=security.hash_password("Pass@1234"),
        role=models.UserRole.candidate,
        must_reset_password=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    session = exam_engine.start_session(db, user, dev=True)
    # Move to intermission
    session.current_part = 2
    from datetime import datetime, timedelta
    session.intermission_ends_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    # Enforce expiry should transition to part 3 with auto-assigned domain
    exam_engine.enforce_expiry(db, session)
    db.refresh(session)
    assert session.current_part == 3
    assert session.paper2_domain is not None
