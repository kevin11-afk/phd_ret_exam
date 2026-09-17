"""
All exam truth lives here and in the database — never in the browser.
"""
import random
from datetime import datetime, timedelta
from typing import Optional

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

from backend import models
from backend.config import EXAM_DURATION_MINUTES, MAX_STRIKES, EXAM_PAPER1_COUNT, EXAM_PAPER2_COUNT, DOMAINS

PART_LABELS = {1: "Part 1 — Research Methodology", 2: "Part 2 — Domain Specific"}


def _is_dev_session(session: models.ExamSession) -> bool:
    if session.started_at and session.phase1_ends_at:
        return (session.phase1_ends_at - session.started_at).total_seconds() <= 120
    return False


def _shuffle_options_for_question(question: models.Question, rng: random.Random) -> list:
    if question.question_type != models.QuestionType.mcq or not question.options:
        return []
    n = len(question.options)
    mapping = list(range(n))
    rng.shuffle(mapping)
    return mapping


def start_session(db: Session, user: models.User, dev: bool = False) -> models.ExamSession:
    existing = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user.id,
                models.ExamSession.status == models.SessionStatus.in_progress)
        .first()
    )
    if existing:
        return existing

    already_used = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user.id,
                models.ExamSession.status.in_([
                    models.SessionStatus.completed,
                    models.SessionStatus.disqualified,
                    models.SessionStatus.expired,
                ]))
        .first()
    )
    if already_used:
        raise ValueError("This candidate has already used their exam attempt.")

    mcq_pool = db.query(models.Question).filter(models.Question.domain == "Research Methodology").all()
    if len(mcq_pool) < EXAM_PAPER1_COUNT:
        raise RuntimeError(
            f"Question bank isn't ready yet: need {EXAM_PAPER1_COUNT} MCQ for RM, currently have {len(mcq_pool)}."
        )

    rng = random.Random(f"{user.id}-{datetime.utcnow().timestamp()}")
    part1 = rng.sample(mcq_pool, EXAM_PAPER1_COUNT)

    option_orders = {}
    for q in part1:
        mapping = _shuffle_options_for_question(q, rng)
        if mapping:
            option_orders[str(q.id)] = mapping

    now = datetime.utcnow()
    session = models.ExamSession(
        user_id=user.id,
        status=models.SessionStatus.in_progress,
        started_at=now,
        phase1_ends_at=now + timedelta(seconds=10 if dev else 60*60),
        question_order=[q.id for q in part1],
        option_orders=option_orders,
        current_index=0,
        strikes=0,
        current_part=1,
        last_activity=now,
    )
    db.add(session)
    db.flush()

    for q in part1:
        db.add(models.Answer(session_id=session.id, question_id=q.id))

    db.commit()
    db.refresh(session)
    return session

def select_domain(db: Session, session: models.ExamSession, domain: str, dev: bool = False) -> models.ExamSession:
    if session.current_part != 2 or session.paper2_domain:
        raise ValueError("Invalid state for domain selection")
        
    pool = db.query(models.Question).filter(models.Question.domain == domain).all()
    if len(pool) < EXAM_PAPER2_COUNT:
        raise ValueError("Paper 2 questions for this domain have not been uploaded yet.")
        
    rng = random.Random(f"{session.user_id}-{datetime.utcnow().timestamp()}")
    part2 = rng.sample(pool, EXAM_PAPER2_COUNT)
    
    new_order = list(session.question_order)
    new_option_orders = dict(session.option_orders)
    
    for q in part2:
        mapping = _shuffle_options_for_question(q, rng)
        if mapping:
            new_option_orders[str(q.id)] = mapping
        new_order.append(q.id)
        db.add(models.Answer(session_id=session.id, question_id=q.id))
        
    session.question_order = new_order
    session.option_orders = new_option_orders
    session.paper2_domain = domain
    session.current_part = 3
    session.phase2_ends_at = datetime.utcnow() + timedelta(seconds=60 if dev else 60*60)
    session.current_index = len(new_order) - EXAM_PAPER2_COUNT # Jump to start of part 2
    
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_active_session(db: Session, user: models.User) -> Optional[models.ExamSession]:
    return (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user.id,
                models.ExamSession.status == models.SessionStatus.in_progress)
        .order_by(models.ExamSession.id.desc())
        .first()
    )


def touch(db: Session, session: models.ExamSession):
    session.last_activity = datetime.utcnow()
    db.add(session)


def time_remaining_seconds(session: models.ExamSession) -> int:
    ends = None
    if session.current_part == 1:
        ends = session.phase1_ends_at
    elif session.current_part == 2:
        ends = session.intermission_ends_at
    elif session.current_part == 3:
        ends = session.phase2_ends_at

    if not ends:
        return 0
    delta = (ends - datetime.utcnow()).total_seconds()
    return max(0, int(delta))


def enforce_expiry(db: Session, session: models.ExamSession) -> models.ExamSession:
    if session.status == models.SessionStatus.in_progress and time_remaining_seconds(session) <= 0:
        dev = _is_dev_session(session)
        if session.current_part == 1:
            session.current_part = 2
            session.intermission_ends_at = datetime.utcnow() + timedelta(seconds=5 if dev else 15 * 60)
        elif session.current_part == 2:
            # Intermission expired: auto-assign default domain and transition to Phase 2/3
            candidate_domain = session.paper2_domain
            if not candidate_domain:
                for d in DOMAINS:
                    cnt = db.query(models.Question).filter(models.Question.domain == d).count()
                    if cnt >= EXAM_PAPER2_COUNT:
                        candidate_domain = d
                        break
                if not candidate_domain:
                    candidate_domain = DOMAINS[0]
            try:
                select_domain(db, session, candidate_domain, dev=dev)
            except Exception:
                session.current_part = 3
                session.paper2_domain = candidate_domain
                session.phase2_ends_at = datetime.utcnow() + timedelta(seconds=60 if dev else 60 * 60)
        elif session.current_part == 3:
            submit_session(db, session)
            session.status = models.SessionStatus.completed
            
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


def get_question_for_display(db: Session, session: models.ExamSession, index: int) -> dict:
    if index < 0 or index >= len(session.question_order):
        raise IndexError("Question index out of range")

    question_id = session.question_order[index]
    question = db.query(models.Question).filter(models.Question.id == question_id).first()
    answer = (
        db.query(models.Answer)
        .filter(models.Answer.session_id == session.id, models.Answer.question_id == question_id)
        .first()
    )

    display_options = None
    if question.question_type == models.QuestionType.mcq:
        mapping = session.option_orders.get(str(question.id), list(range(len(question.options))))
        display_options = [question.options[canon_idx] for canon_idx in mapping]

    if not answer.visited:
        answer.visited = True
        db.add(answer)
        db.commit()

    part = 1 if index < EXAM_PAPER1_COUNT else 2

    return {
        "index": index,
        "total": len(session.question_order),
        "part": part,
        "part_label": PART_LABELS[part],
        "question_type": question.question_type.value,
        "question_text": question.question_text,
        "options": display_options,
        "selected_display_index": answer.selected_display_index,
        "written_text": answer.written_text,
        "marked_for_review": answer.marked_for_review,
    }


def build_nav(db: Session, session: models.ExamSession) -> list:
    answers = {
        a.question_id: a
        for a in db.query(models.Answer).filter(models.Answer.session_id == session.id).all()
    }
    nav = []
    for i, qid in enumerate(session.question_order):
        a = answers.get(qid)
        answered = bool(a and (a.selected_display_index is not None or (a.written_text or "").strip()))
        nav.append({
            "index": i,
            "part": 1 if i < EXAM_PAPER1_COUNT else 2,
            "answered": answered,
            "marked_for_review": bool(a and a.marked_for_review),
            "visited": bool(a and a.visited),
        })
    return nav


def save_answer(db: Session, session: models.ExamSession, index: int,
                 selected_display_index: Optional[int], written_text: Optional[str],
                 marked_for_review: Optional[bool]):
    if index < 0 or index >= len(session.question_order):
        raise IndexError("Question index out of range")
    question_id = session.question_order[index]
    answer = (
        db.query(models.Answer)
        .filter(models.Answer.session_id == session.id, models.Answer.question_id == question_id)
        .first()
    )
    if selected_display_index is not None:
        answer.selected_display_index = selected_display_index
    if written_text is not None:
        answer.written_text = written_text
    if marked_for_review is not None:
        answer.marked_for_review = marked_for_review
    answer.updated_at = datetime.utcnow()
    db.add(answer)
    touch(db, session)
    db.commit()


def navigate(db: Session, session: models.ExamSession, direction: Optional[str], jump_to: Optional[int]) -> int:
    total = len(session.question_order)
    new_index = session.current_index
    if jump_to is not None:
        if jump_to < 0 or jump_to >= total:
            raise IndexError("Question index out of range")
        new_index = jump_to
    elif direction == "next":
        new_index = min(total - 1, session.current_index + 1)
    elif direction == "prev":
        new_index = max(0, session.current_index - 1)
    session.current_index = new_index
    touch(db, session)
    db.commit()
    return new_index


def register_violation(db: Session, session: models.ExamSession, violation_type: str) -> dict:
    if session.status != models.SessionStatus.in_progress:
        return {"strikes": session.strikes, "action": "ignored_session_not_active"}

    session.strikes += 1
    db.add(models.Violation(
        session_id=session.id,
        violation_type=violation_type,
        strike_number=session.strikes,
    ))

    if session.strikes >= MAX_STRIKES:
        session.status = models.SessionStatus.disqualified
        if session.user:
            session.user.token_version += 1
        action = "disqualify"
    else:
        action = "warn"

    touch(db, session)
    db.commit()
    db.refresh(session)
    return {"strikes": session.strikes, "action": action}


def disqualify_session(db: Session, session: models.ExamSession, reason: str = "Admin disqualified") -> models.ExamSession:
    if session.status in (models.SessionStatus.in_progress, models.SessionStatus.not_started):
        session.status = models.SessionStatus.disqualified
        if session.user:
            session.user.token_version += 1
        touch(db, session)
        db.commit()
        db.refresh(session)
    return session


def submit_session(db: Session, session: models.ExamSession):
    if session.status == models.SessionStatus.in_progress:
        session.status = models.SessionStatus.completed
        session.submitted_at = datetime.utcnow()
        
        # Grading Engine
        score = 0
        answers = db.query(models.Answer).filter(models.Answer.session_id == session.id).all()
        answers_map = {a.question_id: a for a in answers}
        
        for q_id in session.question_order:
            ans = answers_map.get(q_id)
            if not ans or ans.selected_display_index is None:
                continue
                
            q = db.query(models.Question).filter(models.Question.id == q_id).first()
            if q and q.question_type == models.QuestionType.mcq and q.correct_option is not None:
                # Map display index to canonical index
                mapping = session.option_orders.get(str(q.id), list(range(len(q.options))))
                if ans.selected_display_index < len(mapping):
                    canonical_idx = mapping[ans.selected_display_index]
                    if canonical_idx == q.correct_option:
                        score += 1
                        
        session.score = score
        db.add(session)
        db.commit()
        db.refresh(session)
    return session

