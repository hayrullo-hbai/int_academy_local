"""One-off script: delete all users + submissions, nullify ownership."""

from sqlalchemy import text
from app.core.database import SessionLocal


def main():
    db = SessionLocal()
    try:
        # 1. Nullify ownership (no users left to own things)
        for tbl, col in [
            ("academy_problems", "owner_id"),
            ("academy_data_problems", "owner_id"),
            ("academy_exams", "owner_id"),
            ("academy_datasets", "uploaded_by_id"),
            ("academy_questions", "problem_id"),
            ("academy_questions", "dataset_id"),
        ]:
            cnt = db.execute(
                text(f"SELECT COUNT(*) FROM {tbl} WHERE {col} IS NOT NULL")
            ).scalar()
            if cnt:
                db.execute(text(f"UPDATE {tbl} SET {col} = NULL"))
                print(f"  {tbl}.{col}: {cnt} rows nullified")

        # 2. Delete submissions
        for tbl in [
            "academy_submissions",
            "academy_data_submissions",
            "academy_exam_submissions",
            "academy_exam_attempts",
        ]:
            cnt = db.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
            if cnt:
                db.execute(text(f"DELETE FROM {tbl}"))
                print(f"  {tbl}: {cnt} rows deleted")

        # 3. Delete onboarding references to users
        cnt = db.execute(
            text("SELECT COUNT(*) FROM onboarding_stage_assignees")
        ).scalar()
        if cnt:
            db.execute(text("DELETE FROM onboarding_stage_assignees"))
            print(f"  onboarding_stage_assignees: {cnt} rows deleted")

        db.execute(text("UPDATE onboarding_stages SET decided_by_id = NULL"))
        db.execute(text("UPDATE onboarding_stage_reports SET author_id = NULL"))
        db.execute(text("UPDATE onboarding_pipelines SET user_id = NULL"))

        # 4. Delete users
        cnt = db.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if cnt:
            db.execute(text("DELETE FROM refresh_tokens"))
            db.execute(text("DELETE FROM password_reset_tokens"))
            db.execute(text("DELETE FROM user_roles"))
            db.execute(text("DELETE FROM users"))
            print(f"  users: {cnt} rows deleted")

        db.commit()
        print("\nDone. All users and submissions cleared.")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
