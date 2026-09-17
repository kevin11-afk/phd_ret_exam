from typing import Optional, List
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, EmailStr, Field, field_validator

from backend.config import DOMAINS


# ---------- Auth ----------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    must_reset_password: bool
    full_name: str


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


# ---------- Exam: candidate-facing ----------
class QuestionOut(BaseModel):
    index: int              # position within this candidate's shuffled exam (0-based)
    total: int
    part: int                # 1 = MCQ section, 2 = written section
    part_label: str
    question_type: str
    question_text: str
    options: Optional[List[str]] = None   # already shuffled for display; None for written
    selected_display_index: Optional[int] = None
    written_text: Optional[str] = None
    marked_for_review: bool = False


class ExamStateOut(BaseModel):
    status: str
    current_part: int
    paper2_domain: Optional[str] = None
    time_remaining_seconds: int
    current_index: int
    total_questions: int
    strikes: int
    max_strikes: int
    nav: List[dict]   # [{index, answered, marked_for_review, visited}]


class AnswerSubmit(BaseModel):
    index: int
    selected_display_index: Optional[int] = None
    written_text: Optional[str] = None
    marked_for_review: Optional[bool] = None


class SelectDomainRequest(BaseModel):
    domain: str

class NavigateRequest(BaseModel):
    direction: Optional[str] = None   # "prev" | "next"
    jump_to: Optional[int] = None


class ViolationReport(BaseModel):
    violation_type: str


class ViolationResult(BaseModel):
    strikes: int
    max_strikes: int
    action: str        # "warn" | "disqualified"
    violation_type: str


# ---------- Admin ----------
class CandidateStatusOut(BaseModel):
    session_id: int
    user_id: int
    full_name: str
    email: str
    status: str
    strikes: int
    max_strikes: int
    answered_count: int
    total_questions: int
    time_remaining_seconds: int
    last_activity_seconds_ago: int
    score: Optional[int] = None


# ---------- Admin: staff & candidate account management ----------
class ManagedUserOut(BaseModel):
    id: int
    full_name: str
    email: str
    role: str
    is_active: bool
    must_reset_password: bool
    # Only meaningful for candidates: status of their most recent exam
    # attempt, if any, so the admin can see who needs re-activating.
    latest_session_status: Optional[str] = None
    latest_session_score: Optional[int] = None


class CreateManagedUserRequest(BaseModel):
    full_name: str = Field(min_length=1)
    email: EmailStr
    role: str  # "setter" or "candidate" — admin accounts are not created here

    @field_validator("role")
    @classmethod
    def role_must_be_manageable(cls, v: str) -> str:
        if v not in ("setter", "candidate"):
            raise ValueError('role must be "setter" or "candidate"')
        return v


class CreateManagedUserResponse(BaseModel):
    user: ManagedUserOut
    temporary_password: str


class UpdateManagedUserRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None


class AdminResetPasswordResponse(BaseModel):
    user_id: int
    email: str
    temporary_password: str


class ReactivateCandidateResponse(BaseModel):
    user_id: int
    email: str
    message: str


# ---------- Admin / setter: question bank ----------
class QuestionIn(BaseModel):
    order_no: int
    domain: str
    question_type: str  # "mcq" | "written"
    question_text: str = Field(min_length=1)
    options: Optional[List[str]] = None
    correct_option: Optional[int] = None

    @field_validator("question_type")
    @classmethod
    def valid_question_type(cls, v: str) -> str:
        if v not in ("mcq", "written"):
            raise ValueError('question_type must be "mcq" or "written"')
        return v

    @field_validator("domain")
    @classmethod
    def valid_domain(cls, v: str) -> str:
        if v not in DOMAINS:
            raise ValueError(f"domain must be one of: {', '.join(DOMAINS)}")
        return v


class QuestionAdminOut(BaseModel):
    id: int
    order_no: int
    domain: str
    question_type: str
    question_text: str
    options: Optional[List[str]] = None
    correct_option: Optional[int] = None


class BulkImportRowError(BaseModel):
    row: int  # 1-based, matching the row number a spreadsheet would show
    message: str


class BulkImportResult(BaseModel):
    created: int
    errors: List[BulkImportRowError]


class QuestionBankMeta(BaseModel):
    domains: List[str]
    question_types: List[str] = ["mcq", "written"]
