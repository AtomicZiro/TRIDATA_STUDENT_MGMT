from pathlib import Path

from flask import Flask

from app.extensions import db, login_manager
from config import Config


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User as UserModel

    @login_manager.user_loader
    def load_user(user_id: str) -> UserModel | None:
        return db.session.get(UserModel, int(user_id))

    from app.routes.auth import bp as auth_bp
    from app.routes.admin import bp as admin_bp
    from app.routes.faculty import bp as faculty_bp
    from app.routes.student import bp as student_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(faculty_bp, url_prefix="/faculty")
    app.register_blueprint(student_bp, url_prefix="/student")

    @app.route("/")
    def index():
        from flask import redirect, url_for
        from flask_login import current_user

        if current_user.is_authenticated:
            if current_user.role == "admin":
                return redirect(url_for("admin.dashboard"))
            if current_user.role == "faculty":
                return redirect(url_for("faculty.dashboard"))
            return redirect(url_for("student.dashboard"))
        return redirect(url_for("auth.login"))

    with app.app_context():
        db.create_all()
        from app.sqlite_migrations import apply_sqlite_migrations

        apply_sqlite_migrations()

    return app
