from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="student")  # admin | faculty | student

    student = db.relationship("Student", backref="user_account", uselist=False)
    faculty = db.relationship("Faculty", backref="user_account", uselist=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Course(db.Model):
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    duration_years = db.Column(db.Integer, default=4)

    subjects = db.relationship("Subject", backref="course", lazy="dynamic")
    students = db.relationship("Student", backref="course", lazy="dynamic")


class Subject(db.Model):
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(32), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    max_internal = db.Column(db.Integer, default=40)
    max_external = db.Column(db.Integer, default=60)

    __table_args__ = (db.UniqueConstraint("course_id", "code", name="uq_subject_course_code"),)

    results = db.relationship("Result", backref="subject", lazy="dynamic")


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    roll_number = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(32))
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)

    results = db.relationship("Result", backref="student", lazy="dynamic", cascade="all, delete-orphan")
    trainings = db.relationship("Training", backref="student", lazy="dynamic", cascade="all, delete-orphan")


class Result(db.Model):
    __tablename__ = "results"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    semester = db.Column(db.Integer, nullable=False)
    academic_year = db.Column(db.String(32), nullable=False, default="2025-26")
    internal_marks = db.Column(db.Float, nullable=False, default=0)
    external_marks = db.Column(db.Float, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint(
            "student_id", "subject_id", "semester", "academic_year", name="uq_result_row"
        ),
    )

    @property
    def total_marks(self) -> float:
        return float(self.internal_marks or 0) + float(self.external_marks or 0)


class Faculty(db.Model):
    __tablename__ = "faculty"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    employee_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    full_name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(120))

    assignments = db.relationship(
        "FacultySubjectAssignment",
        backref="faculty",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class FacultySubjectAssignment(db.Model):
    __tablename__ = "faculty_subject_assignments"

    id = db.Column(db.Integer, primary_key=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey("faculty.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("faculty_id", "subject_id", name="uq_faculty_subject"),
    )

    subject = db.relationship("Subject", backref="faculty_assignments")


class Training(db.Model):
    __tablename__ = "training"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.String(255), default="Weak performance")
    status = db.Column(db.String(32), default="assigned")  # assigned | in_progress | completed
    notes = db.Column(db.Text)
    session_date = db.Column(db.String(32))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    subject = db.relationship("Subject", backref="training_items")
