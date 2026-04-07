import csv
import io
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from sqlalchemy import func

from app.extensions import db
from app.models import Result, Student, Subject, Training
from app.study_year import program_year_from_semester, semester_bounds_for_program_year

bp = Blueprint("student", __name__)


@bp.before_request
@login_required
def _student_gate():
    if current_user.role != "student":
        abort(403)


def _get_student() -> Student:
    st = Student.query.filter_by(user_id=current_user.id).first()
    if not st:
        abort(404)
    return st


def _profile_initials(full_name: str) -> str:
    parts = [p for p in full_name.split() if p]
    return "".join(p[0] for p in parts[:2]).upper()


def _student_report_rows(student_id: int, year: str | None = None, semester: int | None = None):
    q = Result.query.filter_by(student_id=student_id).join(Subject)
    if year:
        q = q.filter(Result.academic_year == year)
    if semester:
        q = q.filter(Result.semester == semester)
    return q.order_by(Result.academic_year, Result.semester, Subject.code).all()


def _report_csv_text(student: Student, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "roll_number",
            "student_name",
            "course",
            "subject_code",
            "subject_name",
            "semester",
            "academic_year",
            "internal_marks",
            "external_marks",
            "total_marks",
        ]
    )
    for row in rows:
        w.writerow(
            [
                student.roll_number,
                student.full_name,
                student.course.name,
                row.subject.code,
                row.subject.name,
                row.semester,
                row.academic_year,
                row.internal_marks,
                row.external_marks,
                row.total_marks,
            ]
        )
    return buf.getvalue()


def _report_pdf_bytes(student: Student, rows, title_suffix: str = "") -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    w, h = A4
    y = h - 40
    c.setFont("Helvetica-Bold", 12)
    c.drawString(
        30,
        y,
        f"Report Card - {student.full_name} ({student.roll_number}){title_suffix}",
    )
    y -= 18
    c.setFont("Helvetica", 9)
    c.drawString(30, y, f"Course: {student.course.code} - {student.course.name}")
    y -= 20

    c.setFont("Helvetica-Bold", 8)
    c.drawString(30, y, "Sub Code")
    c.drawString(90, y, "Subject Name")
    c.drawString(292, y, "Sem")
    c.drawString(322, y, "Year")
    c.drawString(378, y, "Int")
    c.drawString(432, y, "Ext")
    c.drawString(492, y, "Total")
    y -= 12
    c.line(30, y, w - 30, y)
    y -= 10

    c.setFont("Helvetica", 8)
    for row in rows:
        if y < 40:
            c.showPage()
            y = h - 40
            c.setFont("Helvetica", 8)
        c.drawString(30, y, str(row.subject.code))
        c.drawString(90, y, str(row.subject.name)[:36])
        c.drawString(292, y, str(row.semester))
        c.drawString(322, y, str(row.academic_year))
        c.drawRightString(418, y, f"{row.internal_marks:.1f}")
        c.drawRightString(474, y, f"{row.external_marks:.1f}")
        c.drawRightString(535, y, f"{row.total_marks:.1f}")
        y -= 12

    c.save()
    out.seek(0)
    return out.read()


@bp.route("/")
def dashboard():
    student = _get_student()
    rows = Result.query.filter_by(student_id=student.id).join(Subject).order_by(Result.semester).all()
    n = len(rows)
    by_sem = {}
    by_sub: dict[str, list[float]] = {}
    sub_name_by_code: dict[str, str] = {}
    for row in rows:
        max_marks = float(row.subject.max_internal + row.subject.max_external)
        pct = (row.total_marks / max_marks * 100.0) if max_marks else 0.0
        by_sem.setdefault(row.semester, []).append(pct)
        code = row.subject.code
        sub_name_by_code[code] = row.subject.name
        by_sub.setdefault(code, []).append(pct)
    sem_labels = [f"Sem {s}" for s in sorted(by_sem.keys())]
    sem_values = [round(sum(by_sem[s]) / len(by_sem[s]), 1) for s in sorted(by_sem.keys())]
    sub_labels = sorted(by_sub.keys())
    sub_values = [round(sum(by_sub[s]) / len(by_sub[s]), 1) for s in sub_labels]
    sub_subject_names = [sub_name_by_code[c] for c in sub_labels]
    training_items = Training.query.filter_by(student_id=student.id).order_by(Training.id.desc()).limit(5).all()
    return render_template(
        "student/dashboard.html",
        student=student,
        result_count=n,
        sem_labels=sem_labels,
        sem_values=sem_values,
        sub_labels=sub_labels,
        sub_values=sub_values,
        sub_subject_names=sub_subject_names,
        training_items=training_items,
    )


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    student = _get_student()
    if request.method == "POST" and (request.form.get("action") or "").strip() == "change_password":
        current_pw = request.form.get("current_password") or ""
        new_pw = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not current_user.check_password(current_pw):
            flash("Current password is incorrect.", "danger")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "warning")
        elif new_pw != confirm:
            flash("New password and confirmation do not match.", "warning")
        elif current_user.check_password(new_pw):
            flash("New password must be different from your current password.", "warning")
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            flash("Your password has been updated successfully.", "success")
        return redirect(url_for("student.profile"))

    n_results = Result.query.filter_by(student_id=student.id).count()
    n_subjects = (
        db.session.query(func.count(func.distinct(Result.subject_id)))
        .filter(Result.student_id == student.id)
        .scalar()
        or 0
    )
    n_sems = (
        db.session.query(func.count(func.distinct(Result.semester)))
        .filter(Result.student_id == student.id)
        .scalar()
        or 0
    )
    n_training = Training.query.filter_by(student_id=student.id).count()
    max_sem = (
        db.session.query(func.max(Result.semester)).filter(Result.student_id == student.id).scalar()
    )
    program_year_reached = program_year_from_semester(max_sem) if max_sem else None
    course_years = student.course.duration_years or 0

    return render_template(
        "student/profile.html",
        student=student,
        profile_initials=_profile_initials(student.full_name),
        stat_results=n_results,
        stat_subjects=n_subjects,
        stat_semesters=n_sems,
        stat_training=n_training,
        program_year_reached=program_year_reached,
        course_years=course_years,
    )


@bp.route("/results")
def results():
    student = _get_student()
    semester = request.args.get("semester", type=int)
    q = Result.query.filter_by(student_id=student.id).join(Subject)
    if semester:
        q = q.filter(Result.semester == semester)
    results_list = q.order_by(Result.semester, Subject.code).all()
    semesters = (
        db.session.query(Result.semester)
        .filter_by(student_id=student.id)
        .distinct()
        .order_by(Result.semester)
        .all()
    )
    sem_values = [s[0] for s in semesters]
    return render_template(
        "student/results.html",
        student=student,
        results=results_list,
        semesters=sem_values,
        filter_semester=semester,
    )


@bp.route("/report-card")
def report_card():
    student = _get_student()
    year = (request.args.get("year") or "").strip()
    q = (
        Result.query.filter_by(student_id=student.id)
        .join(Subject)
        .order_by(Result.academic_year, Result.semester, Subject.code)
    )
    if year:
        q = q.filter(Result.academic_year == year)
    results_list = q.all()
    by_year_sem = {}
    for r in results_list:
        by_year_sem.setdefault(r.academic_year, {}).setdefault(r.semester, []).append(r)
    years = sorted(by_year_sem.keys())
    totals = {}
    for r in results_list:
        key = (r.academic_year, r.semester)
        totals[key] = totals.get(key, 0.0) + r.total_marks
    return render_template(
        "student/report_card.html",
        student=student,
        by_year_sem=by_year_sem,
        years=years,
        totals=totals,
        filter_year=year or None,
    )


@bp.route("/report-card/download")
def report_card_download():
    student = _get_student()
    year = (request.args.get("year") or "").strip() or None
    semester = request.args.get("semester", type=int)
    fmt = (request.args.get("format") or "csv").strip().lower()

    rows = _student_report_rows(student.id, year=year, semester=semester)
    name_parts = [f"report_{student.roll_number}"]
    if year:
        name_parts.append(year.replace("/", "-"))
    if semester:
        name_parts.append(f"sem{semester}")
    name_parts.append(datetime.now(timezone.utc).strftime("%Y%m%d"))
    base = "_".join(name_parts)

    if fmt == "pdf":
        suffix = ""
        if year:
            suffix += f" - {year}"
        if semester:
            suffix += f" - Semester {semester}"
        pdf_bytes = _report_pdf_bytes(student, rows, suffix)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{base}.pdf",
        )

    csv_text = _report_csv_text(student, rows)
    return send_file(
        io.BytesIO(csv_text.encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{base}.csv",
    )


@bp.route("/training")
def training():
    student = _get_student()
    items = Training.query.filter_by(student_id=student.id).join(Subject).order_by(Training.id.desc()).all()
    return render_template("student/training.html", student=student, items=items)


@bp.route("/analytics")
def analytics():
    student = _get_student()
    study_year = request.args.get("study_year", type=int)
    semester = request.args.get("semester", type=int)

    q = Result.query.filter_by(student_id=student.id).join(Subject)
    if study_year:
        lo, hi = semester_bounds_for_program_year(study_year)
        q = q.filter(Result.semester >= lo, Result.semester <= hi)
    if semester:
        q = q.filter(Result.semester == semester)
    rows = q.order_by(Result.academic_year, Result.semester, Subject.code).all()

    by_subject: dict[str, list[float]] = {}
    subject_name_by_code: dict[str, str] = {}
    by_sem = {}
    by_year = {}
    internal_vals = []
    external_vals = []
    pass_count = 0
    fail_count = 0
    for row in rows:
        max_marks = float(row.subject.max_internal + row.subject.max_external)
        pct = (row.total_marks / max_marks * 100.0) if max_marks else 0.0
        code = row.subject.code
        subject_name_by_code[code] = row.subject.name
        by_subject.setdefault(code, []).append(pct)
        by_sem.setdefault(f"Sem {row.semester}", []).append(pct)
        by_year.setdefault(row.academic_year, []).append(pct)
        internal_vals.append(float(row.internal_marks or 0))
        external_vals.append(float(row.external_marks or 0))
        if pct >= 40.0:
            pass_count += 1
        else:
            fail_count += 1

    subject_labels = sorted(by_subject.keys())
    subject_name_labels = [subject_name_by_code[c] for c in subject_labels]
    subject_avg = [round(sum(v) / len(v), 1) for v in (by_subject[k] for k in subject_labels)]
    sem_labels = sorted(by_sem.keys(), key=lambda x: int(x.split()[-1]))
    sem_avg = [round(sum(v) / len(v), 1) for v in (by_sem[k] for k in sem_labels)]
    year_labels = sorted(by_year.keys())
    year_avg = [round(sum(v) / len(v), 1) for v in (by_year[k] for k in year_labels)]

    avg_internal = round(sum(internal_vals) / len(internal_vals), 1) if internal_vals else 0.0
    avg_external = round(sum(external_vals) / len(external_vals), 1) if external_vals else 0.0

    buckets = {"0-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
    for values in by_subject.values():
        avg_pct = sum(values) / len(values)
        if avg_pct < 40:
            buckets["0-39"] += 1
        elif avg_pct < 60:
            buckets["40-59"] += 1
        elif avg_pct < 80:
            buckets["60-79"] += 1
        else:
            buckets["80-100"] += 1

    semesters_in_rows = {row.semester for row in rows}
    years_in_rows = {row.academic_year for row in rows}
    n_subjects = len(by_subject)
    # Only show charts that are meaningful for a single student's filtered data.
    show_subject_chart = bool(rows)
    show_semester_trend = len(semesters_in_rows) >= 2
    show_year_trend = len(years_in_rows) >= 2
    show_pass_fail = bool(rows)
    show_score_bands = n_subjects >= 2

    filters = {"study_year": study_year, "semester": semester}
    max_sem = (
        db.session.query(func.max(Result.semester)).filter(Result.student_id == student.id).scalar() or 0
    )
    dur = student.course.duration_years or 4
    max_y = program_year_from_semester(max_sem) if max_sem else 1
    year_options = list(range(1, max(dur, max_y, 1) + 1))
    sem_options = [
        s[0]
        for s in db.session.query(Result.semester)
        .filter(Result.student_id == student.id)
        .distinct()
        .order_by(Result.semester)
        .all()
    ]

    return render_template(
        "student/analytics.html",
        student=student,
        filters=filters,
        year_options=year_options,
        sem_options=sem_options,
        row_count=len(rows),
        subject_labels=subject_labels,
        subject_name_labels=subject_name_labels,
        subject_avg=subject_avg,
        sem_labels=sem_labels,
        sem_avg=sem_avg,
        year_labels=year_labels,
        year_avg=year_avg,
        avg_internal=avg_internal,
        avg_external=avg_external,
        pass_fail={"pass": pass_count, "fail": fail_count},
        bucket_labels=list(buckets.keys()),
        bucket_values=list(buckets.values()),
        show_subject_chart=show_subject_chart,
        show_semester_trend=show_semester_trend,
        show_year_trend=show_year_trend,
        show_pass_fail=show_pass_fail,
        show_score_bands=show_score_bands,
    )
