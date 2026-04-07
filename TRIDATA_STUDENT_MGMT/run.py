"""Run: python run.py  |  python run.py init  |  python run.py reset-admin  |  python run.py seed-mca"""

import os
import sys

from sqlalchemy import func

from app import create_app
from app.extensions import db
from app.models import Course, Faculty, FacultySubjectAssignment, Student, Subject, User

app = create_app()

DEFAULT_ADMIN_PASSWORD = "admin123"

# Matches sample_results_upload.csv — course C001 MCA
MCA_COURSE_CODE = "C001"
MCA_COURSE_NAME = "MCA"
MCA_DURATION_YEARS = 3
DEFAULT_STUDENT_PASSWORD = "student123"
DEFAULT_FACULTY_PASSWORD = "faculty123"

MCA_SUBJECTS: list[tuple[str, str, int, int]] = [
    ("CS101", "Problem Solving and Programming", 40, 60),
    ("MA101", "Discrete Mathematical Structures", 40, 60),
    ("PH101", "Engineering Physics", 40, 60),
    ("EC101", "Digital Electronics", 40, 60),
    ("HS101", "Technical Communication", 40, 60),
    ("CS102", "Data Structures", 40, 60),
    ("MA102", "Linear Algebra and Applications", 40, 60),
    ("PH102", "Physics — Module II", 40, 60),
    ("CS201", "Database Management Systems", 40, 60),
    ("MA201", "Probability and Statistics", 40, 60),
]

MCA_STUDENTS: list[tuple[str, str, str, str]] = [
    ("CS001", "cs001", "Arjun Mehta", "cs001@college.edu"),
    ("CS002", "cs002", "Priya Nair", "cs002@college.edu"),
    ("CS003", "cs003", "Rahul Sharma", "cs003@college.edu"),
    ("CS004", "cs004", "Ananya Iyer", "cs004@college.edu"),
]

MCA_FACULTY: list[tuple[str, str, str]] = [
    ("FAC-MATH-01", "Dr. Kavita Menon", "kavita@college.edu"),
    ("FAC-CS-01", "Prof. Rakesh Jain", "rakesh@college.edu"),
]


def init_admin() -> None:
    with app.app_context():
        db.create_all()
        if User.query.filter_by(username="admin").first():
            print("Admin user already exists. If login fails, run: python run.py reset-admin")
            return
        u = User(username="admin", role="admin")
        u.set_password(DEFAULT_ADMIN_PASSWORD)
        db.session.add(u)
        db.session.commit()
        print(
            "Created admin user: username=admin password="
            f"{DEFAULT_ADMIN_PASSWORD} (change after first login)"
        )


def reset_admin_password() -> None:
    """Set admin password from ADMIN_PASSWORD env or default (local dev only)."""
    with app.app_context():
        db.create_all()
        pw = os.environ.get("ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD
        u = User.query.filter_by(username="admin").first()
        if u is None:
            u = User(username="admin", role="admin")
            db.session.add(u)
            action = "Created"
        else:
            action = "Updated"
        u.set_password(pw)
        u.role = "admin"
        db.session.commit()
        src = "ADMIN_PASSWORD env" if os.environ.get("ADMIN_PASSWORD") else "default"
        print(f"{action} admin user. Password set from {src}. Username: admin")


def seed_mca_sample() -> None:
    """Create course C001 (MCA), subjects for sample_results_upload.csv, and demo students."""
    with app.app_context():
        db.create_all()

        course = Course.query.filter_by(code=MCA_COURSE_CODE).first()
        if course is None:
            course = Course(
                name=MCA_COURSE_NAME,
                code=MCA_COURSE_CODE,
                duration_years=MCA_DURATION_YEARS,
            )
            db.session.add(course)
            db.session.flush()
            print(f"Created course {course.code} — {course.name}")
        else:
            course.name = MCA_COURSE_NAME
            course.duration_years = MCA_DURATION_YEARS
            print(f"Using existing course {course.code} — {course.name}")

        for code, title, mx_in, mx_out in MCA_SUBJECTS:
            sub = Subject.query.filter_by(course_id=course.id, code=code).first()
            if sub is None:
                db.session.add(
                    Subject(
                        name=title,
                        code=code,
                        course_id=course.id,
                        max_internal=mx_in,
                        max_external=mx_out,
                    )
                )
                print(f"  + subject {code} — {title}")
            else:
                sub.name = title
                sub.max_internal = mx_in
                sub.max_external = mx_out

        db.session.flush()

        for roll, login, full_name, email in MCA_STUDENTS:
            st = Student.query.filter_by(roll_number=roll).first()
            user_for_login = User.query.filter_by(username=login).first()
            if user_for_login is None:
                user_for_login = User(username=login, role="student")
                user_for_login.set_password(DEFAULT_STUDENT_PASSWORD)
                db.session.add(user_for_login)
                db.session.flush()
            else:
                user_for_login.role = "student"
                user_for_login.set_password(DEFAULT_STUDENT_PASSWORD)
            if st is not None:
                st.full_name = full_name
                st.email = email
                st.course_id = course.id
                st.user_id = user_for_login.id
                print(f"  updated student {roll} (login: {login})")
                continue

            db.session.add(
                Student(
                    user_id=user_for_login.id,
                    roll_number=roll,
                    full_name=full_name,
                    email=email,
                    course_id=course.id,
                )
            )
            print(f"  + student {roll} — {full_name} (login: {login})")

        db.session.flush()

        faculty_by_emp: dict[str, Faculty] = {}
        for emp_id, full_name, email in MCA_FACULTY:
            emp_id = emp_id.strip().upper()
            fac = Faculty.query.filter(func.lower(Faculty.email) == email.lower()).first()
            if fac is None:
                fac = Faculty.query.filter_by(employee_id=emp_id).first()
            if fac is None:
                user = User.query.filter_by(username=emp_id).first()
                if user is None:
                    user = User(username=emp_id, role="faculty")
                    user.set_password(DEFAULT_FACULTY_PASSWORD)
                    db.session.add(user)
                    db.session.flush()
                else:
                    user.role = "faculty"
                    user.username = emp_id
                    user.set_password(DEFAULT_FACULTY_PASSWORD)
                fac = Faculty(
                    user_id=user.id,
                    employee_id=emp_id,
                    full_name=full_name,
                    email=email,
                )
                db.session.add(fac)
                db.session.flush()
            else:
                user = fac.user_account
                user.role = "faculty"
                user.username = emp_id
                user.set_password(DEFAULT_FACULTY_PASSWORD)
                fac.employee_id = emp_id
                fac.full_name = full_name
                fac.email = email
            faculty_by_emp[emp_id] = fac

        db.session.flush()

        cs_fac = faculty_by_emp["FAC-CS-01"]
        math_fac = faculty_by_emp["FAC-MATH-01"]
        cs_codes = {"CS101", "CS102", "CS201", "EC101", "HS101", "PH101", "PH102"}
        for sub in Subject.query.filter_by(course_id=course.id).all():
            faculty_id = cs_fac.id if sub.code in cs_codes else math_fac.id
            link = FacultySubjectAssignment.query.filter_by(
                faculty_id=faculty_id,
                subject_id=sub.id,
            ).first()
            if link is None:
                db.session.add(
                    FacultySubjectAssignment(
                        faculty_id=faculty_id,
                        subject_id=sub.id,
                    )
                )

        db.session.commit()
        print(
            "\nDone. Upload sample_results_upload.csv from Admin, then students can sign in "
            f"(password for all demo students: {DEFAULT_STUDENT_PASSWORD})."
        )
        print(
            "Faculty login examples: FAC-CS-01 / faculty123, FAC-MATH-01 / faculty123 "
            f"(default password {DEFAULT_FACULTY_PASSWORD})"
        )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        init_admin()
    elif len(sys.argv) > 1 and sys.argv[1] == "reset-admin":
        reset_admin_password()
    elif len(sys.argv) > 1 and sys.argv[1] == "seed-mca":
        seed_mca_sample()
    else:
        app.run(debug=True, host="127.0.0.1", port=5000)
