# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

from backend import models, schemas, exam_engine
from backend.database import get_db
from backend.security import get_current_candidate, get_current_user
from backend.config import MAX_STRIKES, VALID_VIOLATION_TYPES
from backend.websocket_manager import manager

router = APIRouter(prefix="/exam", tags=["exam"])


def _require_active_session(db: Session, user: models.User) -> models.ExamSession:
    session = exam_engine.get_active_session(db, user)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active exam session")
    session = exam_engine.enforce_expiry(db, session)
    if session.status != models.SessionStatus.in_progress:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Session is {session.status.value}")
    return session


async def _broadcast_candidate_update(db: Session, session: models.ExamSession, user: models.User):
    nav = exam_engine.build_nav(db, session)
    answered = sum(1 for n in nav if n["answered"])
    await manager.broadcast({
        "type": "candidate_update",
        "session_id": session.id,
        "user_id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "status": session.status.value,
        "strikes": session.strikes,
        "max_strikes": MAX_STRIKES,
        "answered_count": answered,
        "total_questions": len(session.question_order),
        "time_remaining_seconds": exam_engine.time_remaining_seconds(session),
    })


@router.post("/start", response_model=schemas.QuestionOut)
async def start_exam(dev: bool = False, db: Session = Depends(get_db), user: models.User = Depends(get_current_candidate)):
    if user.approval_status != "approved":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your exam access has not yet been approved by the administrator.")
    try:
        session = exam_engine.start_session(db, user, dev=dev)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except RuntimeError as e:
        # Bank doesn't have enough MCQ/written questions yet — an admin
        # problem, not a candidate one, but the candidate is who hits this.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    session = exam_engine.enforce_expiry(db, session)
    if session.status != models.SessionStatus.in_progress:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Session is {session.status.value}")
    q = exam_engine.get_question_for_display(db, session, session.current_index)
    await _broadcast_candidate_update(db, session, user)
    return q


@router.get("/state", response_model=schemas.ExamStateOut)
def get_state(db: Session = Depends(get_db), user: models.User = Depends(get_current_candidate)):
    session = exam_engine.get_active_session(db, user)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active exam session")
    session = exam_engine.enforce_expiry(db, session)
    nav = exam_engine.build_nav(db, session)
    return schemas.ExamStateOut(
        status=session.status.value,
        current_part=session.current_part,
        paper2_domain=session.paper2_domain,
        time_remaining_seconds=exam_engine.time_remaining_seconds(session),
        current_index=session.current_index,
        total_questions=len(session.question_order),
        strikes=session.strikes,
        max_strikes=MAX_STRIKES,
        nav=nav,
    )


@router.get("/question/{index}", response_model=schemas.QuestionOut)
def get_question(index: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_candidate)):
    session = _require_active_session(db, user)
    try:
        return exam_engine.get_question_for_display(db, session, index)
    except IndexError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question index out of range")


@router.post("/answer")
async def submit_answer(
    payload: schemas.AnswerSubmit,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    session = _require_active_session(db, user)
    try:
        exam_engine.save_answer(
            db, session, payload.index,
            payload.selected_display_index, payload.written_text, payload.marked_for_review,
        )
    except IndexError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question index out of range")
    await _broadcast_candidate_update(db, session, user)
    return {"ok": True}


@router.post("/navigate")
async def navigate(
    payload: schemas.NavigateRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    session = _require_active_session(db, user)
    try:
        new_index = exam_engine.navigate(db, session, payload.direction, payload.jump_to)
    except IndexError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question index out of range")
    q = exam_engine.get_question_for_display(db, session, new_index)
    return q


@router.post("/violation", response_model=schemas.ViolationResult)
async def report_violation(
    payload: schemas.ViolationReport,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_candidate),
):
    if payload.violation_type not in VALID_VIOLATION_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown violation type")

    session = exam_engine.get_active_session(db, user)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active exam session")
    session = exam_engine.enforce_expiry(db, session)
    if session.status != models.SessionStatus.in_progress:
        # Session already over (e.g. time expired) — nothing to strike.
        return schemas.ViolationResult(
            strikes=session.strikes, max_strikes=MAX_STRIKES,
            action="ignored", violation_type=payload.violation_type,
        )

    result = exam_engine.register_violation(db, session, payload.violation_type)
    await _broadcast_candidate_update(db, session, user)
    return schemas.ViolationResult(
        strikes=result["strikes"], max_strikes=MAX_STRIKES,
        action=result["action"], violation_type=payload.violation_type,
    )


@router.post("/submit-part1")
async def submit_part1(dev: bool = False, db: Session = Depends(get_db), user: models.User = Depends(get_current_candidate)):
    session = _require_active_session(db, user)
    dev_mode = dev or exam_engine._is_dev_session(session)
    if session.current_part == 1:
        session.current_part = 2
        from datetime import datetime, timedelta
        session.intermission_ends_at = datetime.utcnow() + timedelta(seconds=5 if dev_mode else 15*60)
        db.add(session)
        db.commit()
    await _broadcast_candidate_update(db, session, user)
    return {"status": session.status.value, "current_part": session.current_part}

@router.post("/select-domain")
async def select_domain(payload: schemas.SelectDomainRequest, dev: bool = False, db: Session = Depends(get_db), user: models.User = Depends(get_current_candidate)):
    session = _require_active_session(db, user)
    dev_mode = dev or exam_engine._is_dev_session(session)
    try:
        session = exam_engine.select_domain(db, session, payload.domain, dev=dev_mode)
        await _broadcast_candidate_update(db, session, user)
        return {"ok": True, "status": session.status.value, "current_part": session.current_part}
    except ValueError as e:
        # Return graceful error payload so candidate remains in domain picker
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"ok": False, "error": str(e), "status": session.status.value, "current_part": session.current_part}
        )



@router.post("/submit")
async def submit_exam(db: Session = Depends(get_db), user: models.User = Depends(get_current_candidate)):
    session = exam_engine.get_active_session(db, user)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active exam session")
    session = exam_engine.submit_session(db, session)
    await _broadcast_candidate_update(db, session, user)
    return {"status": session.status.value}

@router.websocket("/ws/candidate")
async def candidate_websocket(websocket: WebSocket, token: str, db: Session = Depends(get_db)):
    try:
        user = get_current_user(token, db)
        if user.role != models.UserRole.candidate:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
            
        session = exam_engine.get_active_session(db, user)
        if not session or session.status != models.SessionStatus.in_progress:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
            
        await manager.connect_candidate(user.id, websocket)
        session_id = session.id
        
        while True:
            try:
                msg_text = await websocket.receive_text()
                import json
                try:
                    data = json.loads(msg_text)
                    if isinstance(data, dict):
                        # JPEG proctor snapshot feed
                        if data.get("type") == "proctor_frame":
                            data["user_id"] = user.id
                            data["session_id"] = session_id
                            await manager.send_to_admins(data)
                            continue
                except json.JSONDecodeError:
                    pass
                
                # If not JSON, it might be legacy fallback
                await manager.broadcast_frame(session_id, msg_text)
            except Exception:
                pass
            
    except WebSocketDisconnect:
        await manager.disconnect_candidate(user.id)
    except Exception:
        await manager.disconnect_candidate(user.id)
