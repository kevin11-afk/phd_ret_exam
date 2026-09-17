import enum
from datetime import datetime

# pyrefly: ignore [missing-import]
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Text, Enum
)
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import relationship

from backend.database import Base


class UserRole(str, enum.Enum):
    candidate = "candidate"    # exam attender
    admin = "admin"
    setter = "setter"


class SessionStatus(str, enum.Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    completed = "completed"
    disqualified = "disqualified"
    expired = "expired"


class QuestionType(str, enum.Enum):
    mcq = "mcq"
    written = "written"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole, native_enum=False), nullable=False, default=UserRole.candidate)
    approval_status = Column(String, nullable=False, default="registered")
    must_reset_password = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    # Monotonic counter, not a timestamp: every JWT embeds the value that
    # was current at issuance, and any request whose embedded value is
    # behind the user's current counter is rejected outright. Bumped on
    # disqualification so every token this candidate holds dies instantly
    # and unambiguously — no clock/timestamp precision edge cases to reason
    # about (a wall-clock comparison can misfire when a revocation and a
    # fresh login land in the same second, which a scripted/automated
    # client can trigger deliberately; a counter can't).
    token_version = Column(Integer, nullable=False, default=0)

    sessions = relationship("ExamSession", back_populates="user")


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(Integer, nullable=False)  # canonical order before shuffle
    domain = Column(String, nullable=False, index=True, default="General")
    question_type = Column(Enum(QuestionType, native_enum=False), nullable=False)
    phase = Column(Integer, nullable=False, default=1)
    question_text = Column(Text, nullable=False)
    # For MCQ: JSON list of option strings, in canonical (unshuffled) order.
    options = Column(JSON, nullable=True)
    # For MCQ: index into `options` (canonical order) that is correct.
    # Not exposed to candidates at any point.
    correct_option = Column(Integer, nullable=True)


class ExamSession(Base):
    __tablename__ = "exam_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(Enum(SessionStatus, native_enum=False), nullable=False, default=SessionStatus.not_started)

    started_at = Column(DateTime, nullable=True)
    phase1_ends_at = Column(DateTime, nullable=True)
    intermission_ends_at = Column(DateTime, nullable=True)
    phase2_ends_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)

    paper2_domain = Column(String, nullable=True)
    current_part = Column(Integer, nullable=False, default=1)
    score = Column(Integer, nullable=True)

    # Ordered list of question IDs, shuffled once at session start.
    question_order = Column(JSON, nullable=True)
    # {question_id: [shuffled option indices mapping display->canonical]}
    option_orders = Column(JSON, nullable=True)

    current_index = Column(Integer, nullable=False, default=0)
    strikes = Column(Integer, nullable=False, default=0)
    last_activity = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="sessions")
    answers = relationship("Answer", back_populates="session", cascade="all, delete-orphan")
    violations = relationship("Violation", back_populates="session", cascade="all, delete-orphan")


class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("exam_sessions.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)

    # For MCQ: the *display* index the candidate clicked (server maps back
    # to canonical option using option_orders before ever comparing/scoring).
    selected_display_index = Column(Integer, nullable=True)
    written_text = Column(Text, nullable=True)

    marked_for_review = Column(Boolean, nullable=False, default=False)
    visited = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    session = relationship("ExamSession", back_populates="answers")


class Violation(Base):
    __tablename__ = "violations"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("exam_sessions.id"), nullable=False)
    violation_type = Column(String, nullable=False)
    strike_number = Column(Integer, nullable=False)  # strike count AFTER this event
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    session = relationship("ExamSession", back_populates="violations")
