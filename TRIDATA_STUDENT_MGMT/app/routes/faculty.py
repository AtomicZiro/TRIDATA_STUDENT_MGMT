from collections import defaultdict

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    Faculty,
    FacultySubjectAssignment,
    Result,
    Student,
    Subject,
    Training,
)

bp = Blueprint("faculty", __name__)


@bp.before_request
@login_required
def _faculty_gate():
    if current_user.role != "faculty":
        abort(403)


def _get_faculty() -> Faculty:
    faculty = Faculty.query.filter_by(user_id=current_user.id).first()
    if not faculty:
        abort(404)
    return faculty


def _assigned_subject_ids(faculty_id: int) -> list[int]:
    rows = FacultySubjectAssignment.query.filter_by(faculty_id=faculty_id).all()
    return [row.subject_id for row in rows]


@bp.route("/")
def dashboard():
    faculty = _get_faculty()
    subject_ids = _assigned_subject_ids(faculty.id)
    result_rows = Result.query.filter(Result.subject_id.in_(subject_ids)).all() if subject_ids else []
    weak_rows = 0
    total = 0.0
    by_subject = defaultdict(list)
    for row in result_rows:
        max_marks = float(row.subject.max_internal + row.subject.max_external)
        pct = (row.total_marks / max_marks * 100.0) if max_marks else 0.0
        total += pct
        subject_label = f"{row.subject.name} ({row.subject.code})"
        by_subject[subject_label].append(pct)
        if row.total_marks < 40:
            weak_rows += 1
    avg_pct = (total / len(result_rows)) if result_rows else 0.0
    class_by_subject = {
        code: round(sum(items) / len(items), 1)
        for code, items in by_subject.items()
    }
    return render_template(
        "faculty/dashboard.html",
        faculty=faculty,
        subject_count=len(subject_ids),
        row_count=len(result_rows),
        weak_rows=weak_rows,
        avg_pct=round(avg_pct, 1),
        class_by_subject=class_by_subject,
    )


@bp.route("/marks", methods=["GET", "POST"])
def marks():
    faculty = _get_faculty()
    subject_ids = _assigned_subject_ids(faculty.id)
    subjects = Subject.query.filter(Subject.id.in_(subject_ids)).order_by(Subject.code).all() if subject_ids else []

    if request.method == "POST":
        subject_id = request.form.get("subject_id", type=int)
        roll = (request.form.get("roll_number") or "").strip()
        semester = request.form.get("semester", type=int) or 1
        year = (request.form.get("academic_year") or "").strip() or "2025-26"
        internal = request.form.get("internal_marks", type=float) or 0.0
        external = request.form.get("external_marks", type=float) or 0.0

        if subject_id not in subject_ids:
            flash("You are not assigned to this subject.", "danger")
            return redirect(request.url)

        student = Student.query.filter_by(roll_number=roll).first()
        subject = Subject.query.get(subject_id)
        if not student or not subject:
            flash("Invalid student or subject.", "warning")
            return redirect(request.url)
        if student.course_id != subject.course_id:
            flash("Student and subject course mismatch.", "warning")
            return redirect(request.url)

        row = Result.query.filter_by(
            student_id=student.id,
            subject_id=subject_id,
            semester=semester,
            academic_year=year,
        ).first()
        if row:
            row.internal_marks = internal
            row.external_marks = external
            msg = "Marks updated."
        else:
            db.session.add(
                Result(
                    student_id=student.id,
                    subject_id=subject_id,
                    semester=semester,
                    academic_year=year,
                    internal_marks=internal,
                    external_marks=external,
                )
            )
            msg = "Marks added."
        db.session.commit()
        flash(msg, "success")
        return redirect(url_for("faculty.marks", subject_id=subject_id))

    selected_subject_id = request.args.get("subject_id", type=int)
    selected_subject = None
    rows = []
    if selected_subject_id and selected_subject_id in subject_ids:
        selected_subject = Subject.query.get(selected_subject_id)
        rows = (
            Result.query.join(Student)
            .filter(Result.subject_id == selected_subject_id)
            .order_by(Student.roll_number, Result.semester)
            .all()
        )
    return render_template(
        "faculty/marks.html",
        faculty=faculty,
        subjects=subjects,
        selected_subject_id=selected_subject_id,
        selected_subject=selected_subject,
        rows=rows,
    )


@bp.route("/performance")
def performance():
    faculty = _get_faculty()
    subject_ids = _assigned_subject_ids(faculty.id)
    subjects = Subject.query.filter(Subject.id.in_(subject_ids)).order_by(Subject.code).all() if subject_ids else []
    selected_subject_id = request.args.get("subject_id", type=int)
    rows = []
    weak_students = []
    chart_labels = []
    chart_values = []
    trend_labels = []
    trend_values = []
    if selected_subject_id and selected_subject_id in subject_ids:
        rows = (
            Result.query.join(Student)
            .filter(Result.subject_id == selected_subject_id)
            .order_by(Student.roll_number, Result.semester)
            .all()
        )
        by_student = defaultdict(list)
        by_sem = defaultdict(list)
        for row in rows:
            max_marks = float(row.subject.max_internal + row.subject.max_external)
            pct = (row.total_marks / max_marks * 100.0) if max_marks else 0.0
            student_label = f"{row.student.roll_number} - {row.student.full_name}"
            by_student[student_label].append(pct)
            by_sem[row.semester].append(pct)
            if row.total_marks < 40:
                weak_students.append(row.student)
        for roll, vals in by_student.items():
            chart_labels.append(roll)
            chart_values.append(round(sum(vals) / len(vals), 1))
        for sem in sorted(by_sem):
            trend_labels.append(f"Sem {sem}")
            trend_values.append(round(sum(by_sem[sem]) / len(by_sem[sem]), 1))

    return render_template(
        "faculty/performance.html",
        faculty=faculty,
        subjects=subjects,
        selected_subject_id=selected_subject_id,
        rows=rows,
        weak_students=weak_students,
        chart_labels=chart_labels,
        chart_values=chart_values,
        trend_labels=trend_labels,
        trend_values=trend_values,
    )


@bp.route("/training", methods=["GET", "POST"])
def training():
    faculty = _get_faculty()
    subject_ids = _assigned_subject_ids(faculty.id)

    if request.method == "POST":
        item_id = request.form.get("training_id", type=int)
        status = (request.form.get("status") or "").strip()
        notes = (request.form.get("notes") or "").strip() or None
        item = Training.query.get_or_404(item_id)
        if item.subject_id not in subject_ids:
            abort(403)
        if status in {"assigned", "in_progress", "completed"}:
            item.status = status
        item.notes = notes
        db.session.commit()
        flash("Training status updated.", "success")
        return redirect(url_for("faculty.training"))

    items = (
        Training.query.join(Student)
        .join(Subject)
        .filter(Training.subject_id.in_(subject_ids))
        .order_by(Training.id.desc())
        .all()
        if subject_ids
        else []
    )
    return render_template("faculty/training.html", faculty=faculty, items=items)
