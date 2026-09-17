"""
Central configuration for the exam platform.

Everything here is read once at import time. In a real deployment, pull
SECRET_KEY and DATABASE_URL from environment variables / a secrets manager —
they are hardcoded here only so the project runs immediately out of the box.
"""
import os

# --- Environment & Security -------------------------------------------
ENV = os.environ.get("ENV", "development").lower()
SECRET_KEY = os.environ.get("EXAM_SECRET_KEY", "CHANGE_THIS_BEFORE_ANY_REAL_USE_1a2b3c4d5e")

if ENV == "production" and SECRET_KEY == "CHANGE_THIS_BEFORE_ANY_REAL_USE_1a2b3c4d5e":
    raise RuntimeError("FATAL: Cannot use default SECRET_KEY in production environment! Please set EXAM_SECRET_KEY.")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 240  # generous, exam sessions can run long

# --- CORS ---------------------------------------------------------------
raw_cors = os.environ.get("CORS_ORIGINS", "*")
CORS_ORIGINS = [o.strip() for o in raw_cors.split(",") if o.strip()]

# --- Database -----------------------------------------------------------
DATABASE_URL = os.environ.get("EXAM_DATABASE_URL", "sqlite:///./exam_platform.db")

# --- Exam rules (server-authoritative) -----------------------------------
EXAM_DURATION_MINUTES = 120
MAX_STRIKES = 3

EXAM_PAPER1_COUNT = 50
EXAM_PAPER2_COUNT = 50

DOMAINS = [
    "Bio-Science",
    "Computer Science & Engineering",
    "Commerce & Management Studies",
    "English",
    "Mathematics",
    "Agricultural Sciences",
    "Tamil",
]

# How stale a candidate's "last seen" can be before the admin grid marks
# them as disconnected (seconds).
CANDIDATE_STALE_SECONDS = 20

# Violation types the client is allowed to report. Anything else is rejected
# so a tampered client can't invent violation types or spam arbitrary strings.
VALID_VIOLATION_TYPES = {
    "tab_switch",
    "fullscreen_exit",
    "window_resize",
    "copy_attempt",
    "right_click",
    "face_not_detected",
    "multiple_faces",
    "looking_away",
}
