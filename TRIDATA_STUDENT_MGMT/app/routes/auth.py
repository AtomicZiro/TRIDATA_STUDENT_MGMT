from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_user, logout_user
from sqlalchemy import func

from app.models import Faculty, Student, User

bp = Blueprint("auth", __name__)


def _safe_next_url(next_url: str | None) -> str | None:
    """Reject absolute and scheme-relative URLs to prevent open redirects."""
    if not next_url:
        return None
    next_url = next_url.strip()
    if not next_url.startswith("/") or next_url.startswith("//"):
        return None
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return None
    return next_url


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login_id = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter(
            func.lower(User.username) == login_id.lower()
        ).first()
        if not user:
            student = Student.query.filter(
                func.lower(Student.roll_number) == login_id.lower()
            ).first()
            if student:
                user = student.user_account
        if not user:
            faculty = Faculty.query.filter(
                Faculty.employee_id.isnot(None),
                func.lower(Faculty.employee_id) == login_id.lower(),
            ).first()
            if faculty:
                user = faculty.user_account
        if user and user.check_password(password):
            login_user(user)
            next_url = _safe_next_url(request.args.get("next"))
            if user.role == "admin":
                return redirect(next_url or url_for("admin.dashboard"))
            if user.role == "faculty":
                return redirect(next_url or url_for("faculty.dashboard"))
            return redirect(next_url or url_for("student.dashboard"))
        flash("Invalid login ID or password.", "danger")
    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
