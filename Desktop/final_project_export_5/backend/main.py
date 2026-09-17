import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from backend.database import Base, engine, SessionLocal
from backend.models import ExamSession, SessionStatus
from backend.routers import auth, exam, admin, users, questions
from backend.seed import seed
from backend.config import CORS_ORIGINS
from backend import exam_engine

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="PhD Entrance Examination Platform")

# Attach CORS middleware with configurable origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

app.include_router(auth.router)
app.include_router(exam.router)
app.include_router(admin.router)
app.include_router(users.router)
app.include_router(questions.router)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
templates = Jinja2Templates(directory=FRONTEND_DIR / "templates")

background_tasks = []


async def session_expiry_worker():
    """Background task running periodically to enforce session timeouts server-side."""
    while True:
        try:
            await asyncio.sleep(5)
            db = SessionLocal()
            try:
                active_sessions = (
                    db.query(ExamSession)
                    .filter(ExamSession.status == SessionStatus.in_progress)
                    .all()
                )
                for s in active_sessions:
                    exam_engine.enforce_expiry(db, s)
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception:
            pass


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    return JSONResponse(
        status_code=500,
        content={"detail": "A database error occurred. Please try again later."},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"An unexpected error occurred: {str(exc)}"},
    )


@app.on_event("startup")
async def on_startup():
    Base.metadata.create_all(bind=engine)
    if os.environ.get("RUN_SEED") == "1":
        seed()
    worker_task = asyncio.create_task(session_expiry_worker())
    background_tasks.append(worker_task)


@app.on_event("shutdown")
async def on_shutdown():
    for task in background_tasks:
        task.cancel()


@app.get("/")
def root():
    return RedirectResponse(url="/login")


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/reset-password")
def reset_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="reset_password.html")


@app.get("/exam")
def exam_page(request: Request):
    return templates.TemplateResponse(request=request, name="exam.html")


@app.get("/admin")
def admin_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_dashboard.html")


@app.get("/setter")
def setter_page(request: Request):
    return templates.TemplateResponse(request=request, name="setter_dashboard.html")
