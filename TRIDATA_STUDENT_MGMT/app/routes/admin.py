import csv
import io
import os
from collections import defaultdict
from datetime import datetime, timezone

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from werkzeug.utils import secure_filename

from app.extensions import db
from app.study_year import program_year_from_semester, semester_bounds_for_program_year
from app.models import (
    Course,
    Faculty,
    FacultySubjectAssignment,
    Result,
    Student,
    Subject,
    Training,
    User,
)

bp = Blueprint("admin", __name__)
DEFAULT_STUDENT_PASSWORD = "student123"
DEFAULT_FACULTY_PASSWORD = "faculty123"


def _normalize_employee_id(raw: str) -> str:
    return (raw or "").strip().upper()


def _form_nonneg_int(name: str, default: int) -> int:
    raw = request.form.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
        return v if v >= 0 else default
    except ValueError:
        return default


def _analytics_filters_from_request():
    course_id = request.args.get("course_id", type=int)
    subject_id = request.args.get("subject_id", type=int)
    student_id = request.args.get("student_id", type=int)
    semester = request.args.get("semester", type=int)
    study_year = request.args.get("study_year", type=int)
    return course_id, subject_id, student_id, semester, study_year


def _analytics_base_query(course_id, subject_id, student_id, semester, study_year):
    query = Result.query.join(Student).join(Subject).join(Course)
    if course_id:
        query = query.filter(Student.course_id == course_id)
    if subject_id:
        query = query.filter(Result.subject_id == subject_id)
    if student_id:
        query = query.filter(Result.student_id == student_id)
    if semester:
        query = query.filter(Result.semester == semester)
    if study_year:
        lo, hi = semester_bounds_for_program_year(study_year)
        query = query.filter(Result.semester >= lo, Result.semester <= hi)
    return query


@bp.before_request
@login_required
def _admin_gate():
    if current_user.role != "admin":
        from flask import abort

        abort(403)


@bp.route("/")
def dashboard():
    result_rows = Result.query.join(Subject).all()
    counts = {
        "students": Student.query.count(),
        "courses": Course.query.count(),
        "subjects": Subject.query.count(),
        "results": Result.query.count(),
    }
    total_obtained = 0.0
    total_max = 0.0
    pass_count = 0
    weak_rows = 0
    by_subject = defaultdict(list)
    by_student_percent = defaultdict(list)
    by_semester_percent = defaultdict(list)

    for row in result_rows:
        max_marks = float(row.subject.max_internal + row.subject.max_external)
        score = float(row.total_marks)
        percent = (score / max_marks * 100.0) if max_marks else 0.0
        total_obtained += score
        total_max += max_marks
        if percent >= 40.0:
            pass_count += 1
        if score < 40.0:
            weak_rows += 1
        subject_label = f"{row.subject.name} ({row.subject.code})"
        by_subject[subject_label].append(percent)
        by_student_percent[row.student_id].append(percent)
        by_semester_percent[row.semester].append(percent)

    avg_marks = (total_obtained / counts["results"]) if counts["results"] else 0.0
    pass_percentage = (pass_count * 100.0 / counts["results"]) if counts["results"] else 0.0

    weak_students = 0
    for percentages in by_student_percent.values():
        student_avg = sum(percentages) / len(percentages)
        if student_avg < 50.0:
            weak_students += 1

    top_subject = "N/A"
    low_subject = "N/A"
    if by_subject:
        avg_by_subject = {
            code: sum(percentages) / len(percentages)
            for code, percentages in by_subject.items()
        }
        top_subject = max(avg_by_subject, key=avg_by_subject.get)
        low_subject = min(avg_by_subject, key=avg_by_subject.get)

    trend_label = "No data"
    if len(by_semester_percent) >= 2:
        sem_keys = sorted(by_semester_percent)
        first_avg = sum(by_semester_percent[sem_keys[0]]) / len(by_semester_percent[sem_keys[0]])
        last_avg = sum(by_semester_percent[sem_keys[-1]]) / len(by_semester_percent[sem_keys[-1]])
        if last_avg > first_avg + 1:
            trend_label = "Improving"
        elif last_avg < first_avg - 1:
            trend_label = "Declining"
        else:
            trend_label = "Stable"

    stats = {
        "avg_marks": round(avg_marks, 1),
        "pass_percentage": round(pass_percentage, 1),
        "weak_rows": weak_rows,
        "weak_students": weak_students,
        "top_subject": top_subject,
        "low_subject": low_subject,
        "trend": trend_label,
    }
    subject_labels = sorted(by_subject.keys())
    subject_avg = [
        round(sum(by_subject[label]) / len(by_subject[label]), 1)
        for label in subject_labels
    ]
    sem_labels = [f"Sem {sem}" for sem in sorted(by_semester_percent.keys())]
    sem_avg = [
        round(sum(by_semester_percent[sem]) / len(by_semester_percent[sem]), 1)
        for sem in sorted(by_semester_percent.keys())
    ]
    pass_fail = {
        "pass": pass_count,
        "fail": max(counts["results"] - pass_count, 0),
    }
    return render_template(
        "admin/dashboard.html",
        counts=counts,
        stats=stats,
        subject_labels=subject_labels,
        subject_avg=subject_avg,
        sem_labels=sem_labels,
        sem_avg=sem_avg,
        pass_fail=pass_fail,
    )


@bp.route("/analytics")
def analytics():
    course_id, subject_id, student_id, semester, study_year = _analytics_filters_from_request()
    query = _analytics_base_query(course_id, subject_id, student_id, semester, study_year)

    rows = query.order_by(Result.semester, Subject.code).all()

    by_subject = defaultdict(list)
    subject_name_by_code = {}
    by_student = defaultdict(list)
    by_semester = defaultdict(list)
    internal_values = []
    external_values = []
    pass_count = 0
    fail_count = 0

    for row in rows:
        max_marks = float(row.subject.max_internal + row.subject.max_external)
        pct = (row.total_marks / max_marks * 100.0) if max_marks else 0.0
        subject_label = row.subject.code
        subject_name_by_code[row.subject.code] = row.subject.name
        student_label = f"{row.student.roll_number} - {row.student.full_name}"
        by_subject[subject_label].append(pct)
        by_student[student_label].append(pct)
        by_semester[f"Sem {row.semester}"].append(pct)
        internal_values.append(float(row.internal_marks or 0))
        external_values.append(float(row.external_marks or 0))
        if pct >= 40.0:
            pass_count += 1
        else:
            fail_count += 1

    subject_labels = sorted(by_subject.keys())
    subject_avg = [round(sum(vals) / len(vals), 1) for vals in (by_subject[k] for k in subject_labels)]
    subject_name_labels = [subject_name_by_code.get(code, code) for code in subject_labels]

    student_labels = sorted(by_student.keys())
    student_avg = [round(sum(vals) / len(vals), 1) for vals in (by_student[k] for k in student_labels)]

    sem_labels = sorted(by_semester.keys(), key=lambda x: int(x.split()[-1]))
    sem_avg = [round(sum(vals) / len(vals), 1) for vals in (by_semester[k] for k in sem_labels)]

    avg_internal = round(sum(internal_values) / len(internal_values), 1) if internal_values else 0
    avg_external = round(sum(external_values) / len(external_values), 1) if external_values else 0

    buckets = {"0-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
    for percentages in by_student.values():
        avg_score = sum(percentages) / len(percentages)
        if avg_score < 40:
            buckets["0-39"] += 1
        elif avg_score < 60:
            buckets["40-59"] += 1
        elif avg_score < 80:
            buckets["60-79"] += 1
        else:
            buckets["80-100"] += 1

    filters = {
        "course_id": course_id,
        "subject_id": subject_id,
        "student_id": student_id,
        "semester": semester,
        "study_year": study_year,
    }
    export_params = {}
    if course_id:
        export_params["course_id"] = course_id
    if subject_id:
        export_params["subject_id"] = subject_id
    if student_id:
        export_params["student_id"] = student_id
    if semester:
        export_params["semester"] = semester
    if study_year:
        export_params["study_year"] = study_year
    courses = Course.query.order_by(Course.code).all()
    subjects = Subject.query.join(Course).order_by(Course.code, Subject.code).all()
    students = Student.query.order_by(Student.roll_number).all()
    max_sem = db.session.query(func.max(Result.semester)).scalar() or 0
    max_y = program_year_from_semester(max_sem) if max_sem else 1
    year_options = list(range(1, max(max_y, 4) + 1))

    return render_template(
        "admin/analytics.html",
        filters=filters,
        export_params=export_params,
        courses=courses,
        subjects=subjects,
        students=students,
        year_options=year_options,
        row_count=len(rows),
        subject_labels=subject_labels,
        subject_name_labels=subject_name_labels,
        subject_avg=subject_avg,
        student_labels=student_labels,
        student_avg=student_avg,
        sem_labels=sem_labels,
        sem_avg=sem_avg,
        avg_internal=avg_internal,
        avg_external=avg_external,
        pass_fail={"pass": pass_count, "fail": fail_count},
        bucket_labels=list(buckets.keys()),
        bucket_values=list(buckets.values()),
    )


@bp.route("/analytics/export.csv")
def analytics_export_csv():
    course_id, subject_id, student_id, semester, study_year = _analytics_filters_from_request()
    rows = (
        _analytics_base_query(course_id, subject_id, student_id, semester, study_year)
        .order_by(Student.roll_number, Result.semester, Subject.code)
        .all()
    )

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "roll_number",
            "student_name",
            "course_code",
            "subject_code",
            "subject_name",
            "semester",
            "academic_year",
            "internal_marks",
            "external_marks",
            "total_marks",
            "percentage",
        ]
    )
    for row in rows:
        max_marks = float(row.subject.max_internal + row.subject.max_external)
        pct = round((row.total_marks / max_marks * 100.0), 2) if max_marks else 0.0
        writer.writerow(
            [
                row.student.roll_number,
                row.student.full_name,
                row.student.course.code,
                row.subject.code,
                row.subject.name,
                row.semester,
                row.academic_year,
                row.internal_marks,
                row.external_marks,
                row.total_marks,
                pct,
            ]
        )
    out.seek(0)
    mem = io.BytesIO(out.getvalue().encode("utf-8-sig"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return send_file(
        mem,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"analytics_export_{stamp}.csv",
    )


# --- Courses ---


@bp.route("/courses")
def courses_list():
    courses = Course.query.order_by(Course.code).all()
    return render_template("admin/courses_list.html", courses=courses)


@bp.route("/courses/add", methods=["GET", "POST"])
def course_add():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        code = (request.form.get("code") or "").strip().upper()
        years = request.form.get("duration_years") or "4"
        try:
            duration = int(years)
        except ValueError:
            duration = 4
        if not name or not code:
            flash("Name and code are required.", "warning")
        elif Course.query.filter_by(code=code).first():
            flash("Course code already exists.", "warning")
        else:
            c = Course(name=name, code=code, duration_years=duration)
            db.session.add(c)
            db.session.commit()
            flash("Course added.", "success")
            return redirect(url_for("admin.courses_list"))
    return render_template("admin/course_form.html", course=None)


@bp.route("/courses/<int:cid>/edit", methods=["GET", "POST"])
def course_edit(cid):
    course = Course.query.get_or_404(cid)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        code = (request.form.get("code") or "").strip().upper()
        years = request.form.get("duration_years") or "4"
        try:
            duration = int(years)
        except ValueError:
            duration = course.duration_years
        other = Course.query.filter(Course.code == code, Course.id != cid).first()
        if not name or not code:
            flash("Name and code are required.", "warning")
        elif other:
            flash("Another course uses this code.", "warning")
        else:
            course.name = name
            course.code = code
            course.duration_years = duration
            db.session.commit()
            flash("Course updated.", "success")
            return redirect(url_for("admin.courses_list"))
    return render_template("admin/course_form.html", course=course)


@bp.route("/courses/<int:cid>/delete", methods=["POST"])
def course_delete(cid):
    course = Course.query.get_or_404(cid)
    if Student.query.filter_by(course_id=cid).first():
        flash("Cannot delete: students are enrolled in this course.", "danger")
        return redirect(url_for("admin.courses_list"))
    Subject.query.filter_by(course_id=cid).delete()
    db.session.delete(course)
    db.session.commit()
    flash("Course removed.", "info")
    return redirect(url_for("admin.courses_list"))


# --- Subjects ---


@bp.route("/subjects")
def subjects_list():
    subjects = (
        Subject.query.join(Course).order_by(Course.code, Subject.code).all()
    )
    courses = Course.query.order_by(Course.code).all()
    return render_template("admin/subjects_list.html", subjects=subjects, courses=courses)


@bp.route("/subjects/add", methods=["GET", "POST"])
def subject_add():
    courses = Course.query.order_by(Course.code).all()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        code = (request.form.get("code") or "").strip().upper()
        course_id = request.form.get("course_id", type=int)
        mi = _form_nonneg_int("max_internal", 40)
        me = _form_nonneg_int("max_external", 60)
        course = Course.query.get(course_id) if course_id else None
        if not name or not code or not course:
            flash("Fill all required fields.", "warning")
        elif Subject.query.filter_by(course_id=course.id, code=code).first():
            flash("Subject code already exists for this course.", "warning")
        else:
            s = Subject(
                name=name,
                code=code,
                course_id=course.id,
                max_internal=mi,
                max_external=me,
            )
            db.session.add(s)
            db.session.commit()
            flash("Subject added.", "success")
            return redirect(url_for("admin.subjects_list"))
    return render_template("admin/subject_form.html", subject=None, courses=courses)


@bp.route("/subjects/<int:sid>/edit", methods=["GET", "POST"])
def subject_edit(sid):
    subject = Subject.query.get_or_404(sid)
    courses = Course.query.order_by(Course.code).all()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        code = (request.form.get("code") or "").strip().upper()
        course_id = request.form.get("course_id", type=int)
        mi = _form_nonneg_int("max_internal", 40)
        me = _form_nonneg_int("max_external", 60)
        course = Course.query.get(course_id) if course_id else None
        dup = (
            Subject.query.filter(
                Subject.course_id == (course.id if course else 0),
                Subject.code == code,
                Subject.id != sid,
            ).first()
        )
        if not name or not code or not course:
            flash("Fill all required fields.", "warning")
        elif dup:
            flash("Subject code already exists for this course.", "warning")
        else:
            subject.name = name
            subject.code = code
            subject.course_id = course.id
            subject.max_internal = mi
            subject.max_external = me
            db.session.commit()
            flash("Subject updated.", "success")
            return redirect(url_for("admin.subjects_list"))
    return render_template("admin/subject_form.html", subject=subject, courses=courses)


@bp.route("/subjects/<int:sid>/delete", methods=["POST"])
def subject_delete(sid):
    subject = Subject.query.get_or_404(sid)
    Result.query.filter_by(subject_id=sid).delete()
    db.session.delete(subject)
    db.session.commit()
    flash("Subject removed.", "info")
    return redirect(url_for("admin.subjects_list"))


# --- Students ---


@bp.route("/students")
def students_list():
    q = (request.args.get("q") or "").strip()
    course_id = request.args.get("course_id", type=int)
    query = Student.query.join(Course)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Student.roll_number.ilike(like),
                Student.full_name.ilike(like),
                Student.email.ilike(like),
            )
        )
    if course_id:
        query = query.filter(Student.course_id == course_id)
    students = query.order_by(Student.roll_number).all()
    courses = Course.query.order_by(Course.code).all()
    return render_template(
        "admin/students_list.html",
        students=students,
        courses=courses,
        q=q,
        course_id=course_id,
    )


def _import_students_csv(path: str, default_course_id: int | None = None) -> tuple[int, int]:
    try:
        text = _read_uploaded_csv_text(path)
    except OSError:
        return 0, 1

    ok, err = 0, 0
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower(): h for h in (reader.fieldnames or [])}

    def col(*keys):
        for k in keys:
            if k in headers:
                return headers[k]
        return None

    roll_c = col("roll_number", "roll", "rollno", "roll no")
    name_c = col("name", "full_name", "student_name")
    email_c = col("email", "mail")
    course_c = col("course_code", "course")
    phone_c = col("phone", "mobile")

    if not roll_c or not name_c:
        return 0, 1

    default_course = Course.query.get(default_course_id) if default_course_id else None

    for row in reader:
        roll = (row.get(roll_c) or "").strip().upper()
        full_name = (row.get(name_c) or "").strip()
        email = (row.get(email_c) or "").strip() if email_c else ""
        phone = (row.get(phone_c) or "").strip() if phone_c else ""
        course_code = (row.get(course_c) or "").strip().upper() if course_c else ""

        if not roll or not full_name:
            err += 1
            continue

        course = None
        if course_code:
            course = Course.query.filter_by(code=course_code).first()
        elif default_course:
            course = default_course

        if not course:
            err += 1
            continue

        user = User.query.filter_by(username=roll).first()
        student = Student.query.filter_by(roll_number=roll).first()

        if student:
            student.full_name = full_name
            student.email = email or None
            student.phone = phone or None
            student.course_id = course.id
            student.user_account.username = roll
            student.user_account.role = "student"
            student.user_account.set_password(DEFAULT_STUDENT_PASSWORD)
            ok += 1
            continue

        if user and user.role != "student":
            err += 1
            continue

        if user is None:
            user = User(username=roll, role="student")
            user.set_password(DEFAULT_STUDENT_PASSWORD)
            db.session.add(user)
            db.session.flush()
        else:
            user.role = "student"
            user.set_password(DEFAULT_STUDENT_PASSWORD)

        db.session.add(
            Student(
                user_id=user.id,
                roll_number=roll,
                full_name=full_name,
                email=email or None,
                phone=phone or None,
                course_id=course.id,
            )
        )
        ok += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return 0, ok + err
    return ok, err


@bp.route("/students/upload", methods=["GET", "POST"])
def students_upload():
    courses = Course.query.order_by(Course.code).all()
    if request.method == "POST":
        f = request.files.get("file")
        default_course_id = request.form.get("default_course_id", type=int)
        if not f or not f.filename:
            flash("Choose a CSV file.", "warning")
            return redirect(request.url)
        name = secure_filename(f.filename)
        if not name.lower().endswith(".csv"):
            flash("File must be a .csv", "warning")
            return redirect(request.url)
        path = os.path.join(current_app.config["UPLOAD_FOLDER"], name)
        f.save(path)
        rows_ok, rows_err = _import_students_csv(path, default_course_id=default_course_id)
        os.remove(path)
        flash(
            f"Imported/updated {rows_ok} student row(s). {rows_err} error(s). "
            f"Default login password set to {DEFAULT_STUDENT_PASSWORD}.",
            "success" if rows_err == 0 else "warning",
        )
        return redirect(url_for("admin.students_list"))
    return render_template("admin/students_upload.html", courses=courses)


@bp.route("/students/add", methods=["GET", "POST"])
def student_add():
    courses = Course.query.order_by(Course.code).all()
    if request.method == "POST":
        roll = (request.form.get("roll_number") or "").strip().upper()
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip() or None
        phone = (request.form.get("phone") or "").strip() or None
        course_id = request.form.get("course_id", type=int)
        course = Course.query.get(course_id) if course_id else None
        roll = roll.upper()
        if not all([roll, full_name, course]):
            flash("Roll number, name, and course are required.", "warning")
        elif User.query.filter_by(username=roll).first():
            flash("Roll number already used as login ID.", "warning")
        elif Student.query.filter_by(roll_number=roll).first():
            flash("Roll number already exists.", "warning")
        else:
            u = User(username=roll, role="student")
            u.set_password(DEFAULT_STUDENT_PASSWORD)
            db.session.add(u)
            db.session.flush()
            st = Student(
                user_id=u.id,
                roll_number=roll,
                full_name=full_name,
                email=email,
                phone=phone,
                course_id=course.id,
            )
            db.session.add(st)
            db.session.commit()
            flash(
                f"Student added. Login with roll number and password {DEFAULT_STUDENT_PASSWORD}.",
                "success",
            )
            return redirect(url_for("admin.students_list"))
    return render_template("admin/student_form.html", student=None, courses=courses)


@bp.route("/students/<int:sid>/edit", methods=["GET", "POST"])
def student_edit(sid):
    student = Student.query.get_or_404(sid)
    courses = Course.query.order_by(Course.code).all()
    if request.method == "POST":
        roll = (request.form.get("roll_number") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip() or None
        phone = (request.form.get("phone") or "").strip() or None
        course_id = request.form.get("course_id", type=int)
        course = Course.query.get(course_id) if course_id else None
        dup_roll = (
            Student.query.filter(Student.roll_number == roll, Student.id != sid).first()
        )
        user_dup = User.query.filter(User.username == roll, User.id != student.user_id).first()
        if not roll or not full_name or not course:
            flash("Roll number, name, and course are required.", "warning")
        elif dup_roll:
            flash("Another student has this roll number.", "warning")
        elif user_dup:
            flash("Another user already uses this roll number as login ID.", "warning")
        else:
            student.roll_number = roll
            student.full_name = full_name
            student.email = email
            student.phone = phone
            student.course_id = course.id
            student.user_account.username = roll
            if request.form.get("reset_password") == "1":
                student.user_account.set_password(DEFAULT_STUDENT_PASSWORD)
            db.session.commit()
            flash("Student updated.", "success")
            return redirect(url_for("admin.students_list"))
    return render_template("admin/student_form.html", student=student, courses=courses)


@bp.route("/students/<int:sid>/delete", methods=["POST"])
def student_delete(sid):
    student = Student.query.get_or_404(sid)
    user = student.user_account
    Result.query.filter_by(student_id=sid).delete()
    db.session.delete(student)
    db.session.delete(user)
    db.session.commit()
    flash("Student and login removed.", "info")
    return redirect(url_for("admin.students_list"))


# --- Faculty ---


@bp.route("/faculty")
def faculty_list():
    faculty_items = Faculty.query.order_by(Faculty.full_name).all()
    return render_template("admin/faculty_list.html", faculty_items=faculty_items)


@bp.route("/faculty/add", methods=["GET", "POST"])
def faculty_add():
    if request.method == "POST":
        employee_id = _normalize_employee_id(request.form.get("employee_id") or "")
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        if not employee_id or not full_name or not email:
            flash("Faculty name, employee ID, and email are required.", "warning")
        elif User.query.filter(func.lower(User.username) == employee_id.lower()).first():
            flash("That employee ID is already in use as a login.", "warning")
        elif Faculty.query.filter(func.lower(Faculty.employee_id) == employee_id.lower()).first():
            flash("That employee ID already exists.", "warning")
        else:
            user = User(username=employee_id, role="faculty")
            user.set_password(DEFAULT_FACULTY_PASSWORD)
            db.session.add(user)
            db.session.flush()
            db.session.add(
                Faculty(
                    user_id=user.id,
                    employee_id=employee_id,
                    full_name=full_name,
                    email=email,
                )
            )
            db.session.commit()
            flash(
                f"Faculty added. Login with employee ID and default password {DEFAULT_FACULTY_PASSWORD}.",
                "success",
            )
            return redirect(url_for("admin.faculty_list"))
    return render_template("admin/faculty_form.html", item=None)


@bp.route("/faculty/<int:fid>/edit", methods=["GET", "POST"])
def faculty_edit(fid):
    item = Faculty.query.get_or_404(fid)
    if request.method == "POST":
        employee_id = _normalize_employee_id(request.form.get("employee_id") or "")
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        reset_pw = request.form.get("reset_password") == "1"
        if not employee_id or not full_name or not email:
            flash("Faculty name, employee ID, and email are required.", "warning")
        else:
            other_user = (
                User.query.filter(
                    func.lower(User.username) == employee_id.lower(),
                    User.id != item.user_id,
                ).first()
            )
            other_fac = (
                Faculty.query.filter(
                    func.lower(Faculty.employee_id) == employee_id.lower(),
                    Faculty.id != item.id,
                ).first()
            )
            if other_user or other_fac:
                flash("That employee ID is already in use.", "warning")
            else:
                item.employee_id = employee_id
                item.full_name = full_name
                item.email = email
                item.user_account.username = employee_id
                if reset_pw:
                    item.user_account.set_password(DEFAULT_FACULTY_PASSWORD)
                db.session.commit()
                flash("Faculty updated.", "success")
                return redirect(url_for("admin.faculty_list"))
    return render_template("admin/faculty_form.html", item=item)


@bp.route("/faculty/<int:fid>/delete", methods=["POST"])
def faculty_delete(fid):
    item = Faculty.query.get_or_404(fid)
    user = item.user_account
    FacultySubjectAssignment.query.filter_by(faculty_id=fid).delete()
    db.session.delete(item)
    db.session.delete(user)
    db.session.commit()
    flash("Faculty removed.", "info")
    return redirect(url_for("admin.faculty_list"))


@bp.route("/faculty/assignments", methods=["GET", "POST"])
def faculty_assignments():
    faculties = Faculty.query.order_by(Faculty.full_name).all()
    subjects = Subject.query.join(Course).order_by(Course.code, Subject.code).all()
    if request.method == "POST":
        faculty_id = request.form.get("faculty_id", type=int)
        subject_id = request.form.get("subject_id", type=int)
        if not faculty_id or not subject_id:
            flash("Select faculty and subject.", "warning")
        else:
            exists = FacultySubjectAssignment.query.filter_by(
                faculty_id=faculty_id,
                subject_id=subject_id,
            ).first()
            if exists:
                flash("Assignment already exists.", "info")
            else:
                db.session.add(
                    FacultySubjectAssignment(
                        faculty_id=faculty_id,
                        subject_id=subject_id,
                    )
                )
                db.session.commit()
                flash("Subject assigned to faculty.", "success")
        return redirect(url_for("admin.faculty_assignments"))

    assignments = (
        FacultySubjectAssignment.query.join(Faculty)
        .join(Subject)
        .join(Course)
        .order_by(Faculty.full_name, Course.code, Subject.code)
        .all()
    )
    return render_template(
        "admin/faculty_assignments.html",
        faculties=faculties,
        subjects=subjects,
        assignments=assignments,
    )


@bp.route("/faculty/assignments/<int:aid>/delete", methods=["POST"])
def faculty_assignment_delete(aid):
    row = FacultySubjectAssignment.query.get_or_404(aid)
    db.session.delete(row)
    db.session.commit()
    flash("Assignment removed.", "info")
    return redirect(url_for("admin.faculty_assignments"))


# --- Training ---


@bp.route("/training", methods=["GET", "POST"])
def training_list():
    if request.method == "POST":
        student_id = request.form.get("student_id", type=int)
        subject_id = request.form.get("subject_id", type=int)
        reason = (request.form.get("reason") or "").strip() or "Weak performance"
        session_date = (request.form.get("session_date") or "").strip() or None
        if not student_id or not subject_id:
            flash("Select student and subject.", "warning")
        else:
            db.session.add(
                Training(
                    student_id=student_id,
                    subject_id=subject_id,
                    assigned_by=current_user.id,
                    reason=reason,
                    session_date=session_date,
                    status="assigned",
                )
            )
            db.session.commit()
            flash("Training assigned.", "success")
        return redirect(url_for("admin.training_list"))

    items = (
        Training.query.join(Student)
        .join(Subject)
        .order_by(Training.id.desc())
        .all()
    )
    students = Student.query.order_by(Student.roll_number).all()
    subjects = Subject.query.join(Course).order_by(Course.code, Subject.code).all()
    return render_template(
        "admin/training_list.html",
        items=items,
        students=students,
        subjects=subjects,
    )


@bp.route("/training/<int:tid>/status", methods=["POST"])
def training_status_update(tid):
    item = Training.query.get_or_404(tid)
    status = (request.form.get("status") or "").strip()
    if status in {"assigned", "in_progress", "completed"}:
        item.status = status
        db.session.commit()
        flash("Training status updated.", "success")
    else:
        flash("Invalid status.", "warning")
    return redirect(url_for("admin.training_list"))


@bp.route("/training/auto-assign", methods=["POST"])
def training_auto_assign():
    created = 0
    rows = Result.query.join(Subject).all()
    for row in rows:
        if row.total_marks >= 40:
            continue
        exists = Training.query.filter_by(
            student_id=row.student_id,
            subject_id=row.subject_id,
            status="assigned",
        ).first()
        if exists:
            continue
        db.session.add(
            Training(
                student_id=row.student_id,
                subject_id=row.subject_id,
                assigned_by=current_user.id,
                reason="Auto-assigned: marks below 40",
                status="assigned",
            )
        )
        created += 1
    db.session.commit()
    flash(f"Auto-assigned {created} training item(s).", "success")
    return redirect(url_for("admin.training_list"))


# --- Results ---


@bp.route("/results")
def results_list():
    roll = (request.args.get("roll") or "").strip()
    semester = request.args.get("semester", type=int)
    year = (request.args.get("year") or "").strip()
    query = Result.query.join(Student).join(Subject)
    if roll:
        query = query.filter(Student.roll_number.ilike(f"%{roll}%"))
    if semester:
        query = query.filter(Result.semester == semester)
    if year:
        query = query.filter(Result.academic_year == year)
    results = query.order_by(
        Student.roll_number, Result.semester, Subject.code
    ).all()
    students = Student.query.order_by(Student.roll_number).limit(500).all()
    return render_template(
        "admin/results_list.html",
        results=results,
        students=students,
        roll=roll,
        semester=semester,
        year=year,
    )


@bp.route("/results/upload", methods=["GET", "POST"])
def results_upload():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a CSV file.", "warning")
            return redirect(request.url)
        name = secure_filename(f.filename)
        if not name.lower().endswith(".csv"):
            flash("File must be a .csv", "warning")
            return redirect(request.url)
        path = os.path.join(current_app.config["UPLOAD_FOLDER"], name)
        f.save(path)
        rows_ok, rows_err = _import_results_csv(path)
        os.remove(path)
        flash(f"Imported {rows_ok} row(s). {rows_err} error(s).", "success" if rows_err == 0 else "warning")
        return redirect(url_for("admin.results_list"))
    return render_template("admin/results_upload.html")


def _read_uploaded_csv_text(path: str) -> str:
    with open(path, "rb") as bf:
        raw = bf.read()
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _import_results_csv(path: str) -> tuple[int, int]:
    ok, err = 0, 0
    try:
        text = _read_uploaded_csv_text(path)
    except OSError:
        return 0, 1
    fp = io.StringIO(text)
    reader = csv.DictReader(fp)
    headers = {h.strip().lower(): h for h in (reader.fieldnames or [])}

    def col(key_variants):
        for k in key_variants:
            if k in headers:
                return headers[k]
        return None

    roll_c = col(["roll_number", "roll", "roll no", "rollno"])
    sub_c = col(["subject_code", "subject", "code"])
    sem_c = col(["semester", "sem"])
    int_c = col(["internal_marks", "internal", "int"])
    ext_c = col(["external_marks", "external", "ext"])
    year_c = col(["academic_year", "year", "academic year"])

    if not roll_c or not sub_c or not sem_c:
        return 0, 1

    for row in reader:
        try:
            roll = (row.get(roll_c) or "").strip()
            scode = (row.get(sub_c) or "").strip().upper()
            sem_raw = (row.get(sem_c) or "").strip()
            semester = int(sem_raw)
            internal = float((row.get(int_c) or "0").strip() or 0) if int_c else 0.0
            external = float((row.get(ext_c) or "0").strip() or 0) if ext_c else 0.0
            academic_year = (
                (row.get(year_c) or "").strip() if year_c else ""
            ) or "2025-26"
        except (ValueError, TypeError):
            err += 1
            continue

        student = Student.query.filter_by(roll_number=roll).first()
        if not student:
            err += 1
            continue
        subject = Subject.query.filter_by(
            course_id=student.course_id, code=scode
        ).first()
        if not subject:
            err += 1
            continue

        existing = Result.query.filter_by(
            student_id=student.id,
            subject_id=subject.id,
            semester=semester,
            academic_year=academic_year,
        ).first()
        if existing:
            existing.internal_marks = internal
            existing.external_marks = external
        else:
            db.session.add(
                Result(
                    student_id=student.id,
                    subject_id=subject.id,
                    semester=semester,
                    academic_year=academic_year,
                    internal_marks=internal,
                    external_marks=external,
                )
            )
        ok += 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return 0, ok + err
    return ok, err


def _student_results_rows(sid: int, semester: int | None = None):
    query = Result.query.filter_by(student_id=sid).join(Subject)
    if semester:
        query = query.filter(Result.semester == semester)
    return query.order_by(Result.semester, Subject.code).all()


def _student_results_csv_content(student: Student, results, semester: int | None = None) -> str:
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
            "total",
        ]
    )
    for r in results:
        w.writerow(
            [
                student.roll_number,
                student.full_name,
                student.course.name,
                r.subject.code,
                r.subject.name,
                r.semester,
                r.academic_year,
                r.internal_marks,
                r.external_marks,
                r.total_marks,
            ]
        )
    return buf.getvalue()


def _student_results_pdf_bytes(student: Student, results, semester: int | None = None) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    w, h = A4
    y = h - 40
    title = f"Result Report - {student.full_name} ({student.roll_number})"
    if semester:
        title += f" - Semester {semester}"
    c.setFont("Helvetica-Bold", 12)
    c.drawString(30, y, title)
    y -= 18
    c.setFont("Helvetica", 9)
    c.drawString(30, y, f"Course: {student.course.code} - {student.course.name}")
    y -= 20

    c.setFont("Helvetica-Bold", 8)
    c.drawString(30, y, "Sub Code")
    c.drawString(88, y, "Subject Name")
    c.drawString(290, y, "Sem")
    c.drawString(320, y, "Year")
    c.drawString(375, y, "Internal")
    c.drawString(430, y, "External")
    c.drawString(490, y, "Total")
    y -= 12
    c.line(30, y, w - 30, y)
    y -= 10

    c.setFont("Helvetica", 8)
    for r in results:
        if y < 40:
            c.showPage()
            y = h - 40
            c.setFont("Helvetica", 8)
        c.drawString(30, y, str(r.subject.code))
        c.drawString(88, y, str(r.subject.name)[:38])
        c.drawString(290, y, str(r.semester))
        c.drawString(320, y, str(r.academic_year))
        c.drawRightString(418, y, f"{r.internal_marks:.1f}")
        c.drawRightString(478, y, f"{r.external_marks:.1f}")
        c.drawRightString(535, y, f"{r.total_marks:.1f}")
        y -= 12

    c.save()
    out.seek(0)
    return out.read()


@bp.route("/students/<int:sid>/results/download")
def student_results_download(sid):
    student = Student.query.get_or_404(sid)
    semester = request.args.get("semester", type=int)
    fmt = (request.args.get("format") or "csv").strip().lower()
    results = _student_results_rows(sid, semester)

    base = f"results_{student.roll_number}"
    if semester:
        base += f"_sem{semester}"
    base += f"_{datetime.now(timezone.utc).strftime('%Y%m%d')}"

    if fmt == "pdf":
        pdf_bytes = _student_results_pdf_bytes(student, results, semester)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{base}.pdf",
        )

    csv_text = _student_results_csv_content(student, results, semester)
    return send_file(
        io.BytesIO(csv_text.encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{base}.csv",
    )


@bp.route("/students/<int:sid>/results")
def student_results_view(sid):
    student = Student.query.get_or_404(sid)
    results = (
        Result.query.filter_by(student_id=sid)
        .join(Subject)
        .order_by(Result.semester, Subject.code)
        .all()
    )
    by_sem = {}
    for r in results:
        by_sem.setdefault(r.semester, []).append(r)
    return render_template(
        "admin/student_results.html",
        student=student,
        by_sem=by_sem,
    )
