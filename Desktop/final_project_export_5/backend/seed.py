import os
from pathlib import Path
import openpyxl
from backend.database import SessionLocal, engine, Base
from backend import models
from backend.security import hash_password
from backend.services.ingestion import parse_docx_questions

DEMO_ADMIN_PASSWORD = "Admin@123"
BASE_DIR = Path(__file__).resolve().parent.parent


def seed(drop_tables: bool = False):
    if drop_tables or os.environ.get("DROP_BEFORE_SEED") == "1":
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # 1. Admin account
        if not db.query(models.User).filter(models.User.email == "admin@examportal.edu").first():
            admin = models.User(
                email="admin@examportal.edu",
                full_name="Exam Administrator",
                hashed_password=hash_password(DEMO_ADMIN_PASSWORD),
                role=models.UserRole.admin,
                approval_status="approved",
                must_reset_password=False,
            )
            db.add(admin)

        # 2. Candidate 1 (matches smoke_test.sh)
        if not db.query(models.User).filter(models.User.email == "candidate1@examportal.edu").first():
            candidate1 = models.User(
                email="candidate1@examportal.edu",
                full_name="Candidate One",
                hashed_password=hash_password("Welcome@123"),
                role=models.UserRole.candidate,
                approval_status="registered",
                must_reset_password=True,
            )
            db.add(candidate1)

        # 3. Tester account
        if not db.query(models.User).filter(models.User.email == "tester@dsu.edu.in").first():
            tester = models.User(
                email="tester@dsu.edu.in",
                full_name="Exam Tester",
                hashed_password=hash_password("Tester@123"),
                role=models.UserRole.candidate,
                approval_status="approved",
                must_reset_password=False,
            )
            db.add(tester)

        # 4. Candidates from Excel
        count = 0
        default_candidate_pwd = hash_password("Password@123")
        xlsx_path = BASE_DIR / "Application Details (23).xlsx"
        if xlsx_path.exists():
            wb = openpyxl.load_workbook(str(xlsx_path))
            sheet = wb.active
            start_reading = False
            for row in sheet.iter_rows(values_only=True):
                if not row[0]:
                    continue
                if str(row[0]).strip() == 'S.No':
                    start_reading = True
                    continue
                if start_reading:
                    first_name = row[3] or ""
                    last_name = row[5] or ""
                    email = str(row[7]).strip()
                    if email and email != "None":
                        if not db.query(models.User).filter(models.User.email == email).first():
                            user = models.User(
                                email=email,
                                full_name=f"{first_name} {last_name}".strip(),
                                hashed_password=default_candidate_pwd,
                                role=models.UserRole.candidate,
                                approval_status="registered",
                                must_reset_password=True,
                            )
                            db.add(user)
                            count += 1
                    if count >= 50:
                        break

        # 5. Ingest Questions
        rm_path = BASE_DIR / "DSU_Research_Methodology_Set_II.docx"
        cs_path = BASE_DIR / "COMPUTER SCIENCE ENTRANCE TEST QUESTIONS.docx"

        q1_count = 0
        q2_count = 0
        if rm_path.exists() and db.query(models.Question).filter(models.Question.domain == "Research Methodology").count() == 0:
            with open(rm_path, "rb") as f:
                q1 = parse_docx_questions(f.read(), "Research Methodology", phase=1)
                db.add_all(q1)
                q1_count = len(q1)

        if cs_path.exists() and db.query(models.Question).filter(models.Question.domain == "Computer Science & Engineering").count() == 0:
            with open(cs_path, "rb") as f:
                q2 = parse_docx_questions(f.read(), "Computer Science & Engineering", phase=2)
                db.add_all(q2)
                q2_count = len(q2)

        db.commit()
        print(f"Seeded successfully! {count} candidates, {q1_count} RM questions, {q2_count} CS questions.")
    finally:
        db.close()


if __name__ == "__main__":
    seed(drop_tables=True)
