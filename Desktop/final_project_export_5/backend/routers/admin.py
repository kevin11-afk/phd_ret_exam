from datetime import datetime
from typing import List
import csv
import io

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, UploadFile, File, Form, HTTPException, status
from sqlalchemy.orm import Session

from backend import models, schemas, exam_engine
from backend.database import get_db, SessionLocal
from backend import security
from backend.config import MAX_STRIKES
from backend.websocket_manager import manager
from backend.services.ingestion import parse_docx_questions, parse_csv_questions, VALID_DOMAINS

router = APIRouter(tags=["admin"])


@router.get("/admin/sessions", response_model=List[schemas.CandidateStatusOut])
def list_sessions(db: Session = Depends(get_db), admin: models.User = Depends(security.get_current_admin)):
    sessions = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.status != models.SessionStatus.not_started)
        .all()
    )
    out = []
    for s in sessions:
        s = exam_engine.enforce_expiry(db, s)
        nav = exam_engine.build_nav(db, s)
        answered = sum(1 for n in nav if n["answered"])
        seconds_ago = int((datetime.utcnow() - s.last_activity).total_seconds())
        out.append(schemas.CandidateStatusOut(
            session_id=s.id,
            user_id=s.user_id,
            full_name=s.user.full_name,
            email=s.user.email,
            status=s.status.value,
            strikes=s.strikes,
            max_strikes=MAX_STRIKES,
            answered_count=answered,
            total_questions=len(s.question_order or []),
            time_remaining_seconds=exam_engine.time_remaining_seconds(s),
            last_activity_seconds_ago=seconds_ago,
            score=s.score,
        ))
    return out


@router.post("/admin/sessions/{session_id}/disqualify")
async def disqualify_candidate_session(
    session_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    session = db.query(models.ExamSession).filter(models.ExamSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam session not found")

    exam_engine.disqualify_session(db, session, reason="Admin manual disqualification")
    nav = exam_engine.build_nav(db, session)
    answered = sum(1 for n in nav if n["answered"])
    await manager.broadcast({
        "type": "candidate_update",
        "session_id": session.id,
        "user_id": session.user_id,
        "full_name": session.user.full_name,
        "email": session.user.email,
        "status": session.status.value,
        "strikes": session.strikes,
        "max_strikes": MAX_STRIKES,
        "answered_count": answered,
        "total_questions": len(session.question_order or []),
        "time_remaining_seconds": exam_engine.time_remaining_seconds(session),
        "score": session.score,
    })
    return {"status": "disqualified", "session_id": session_id}


@router.post("/admin/questions/upload")
async def upload_questions(
    file: UploadFile = File(...),
    domain: str = Form(...),
    phase: int = Form(...),
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin)
):
    if domain not in VALID_DOMAINS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Domain '{domain}' is not in configured DOMAINS: {', '.join(sorted(VALID_DOMAINS))}"
        )
    if phase not in (1, 2):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phase must be 1 or 2")

    contents = await file.read()
    filename = file.filename.lower()

    try:
        if filename.endswith(".docx"):
            questions = parse_docx_questions(contents, domain, phase)
        elif filename.endswith(".csv"):
            questions = parse_csv_questions(contents, domain, phase)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type (must be .docx or .csv)")

        for q in questions:
            db.add(q)
        db.commit()
        return {"status": "success", "inserted": len(questions)}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/admin/users/upload")
async def upload_users(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin)
):
    contents = await file.read()
    try:
        text = contents.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be valid UTF-8 text")

    reader = csv.DictReader(io.StringIO(text))
    inserted = 0
    default_hash = security.hash_password("DefaultPass@2026")

    for row in reader:
        email = row.get("email", "").strip()
        name = row.get("name", "").strip()
        if not email or not name:
            continue

        existing = db.query(models.User).filter(models.User.email == email).first()
        if not existing:
            user = models.User(
                email=email,
                full_name=name,
                hashed_password=default_hash,
                role=models.UserRole.candidate,
                must_reset_password=True,
                is_active=True
            )
            db.add(user)
            inserted += 1

    db.commit()
    return {"status": "success", "inserted": inserted}


@router.websocket("/ws/admin")
async def admin_ws(websocket: WebSocket, token: str = Query(...)):
    db = SessionLocal()
    try:
        security.decode_token_for_ws(token, db)
    except Exception:
        await websocket.close(code=4401)
        return
    finally:
        db.close()

    await manager.connect(websocket)
    try:
        while True:
            # JPEG snapshot feed and update messages are broadcast from candidates to admin
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
