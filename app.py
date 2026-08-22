import os
import sys

# Make sure this file's own folder is on sys.path, so imports like
# "from extensions import db" work even if the WSGI server launches this
# module from a different working directory (this is what caused the
# "ModuleNotFoundError: No module named 'extensions'" error on PythonAnywhere).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask

from config import Config
from extensions import db, login_manager
from models import User, SiteSetting


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)

    os.makedirs(Config.UPLOAD_AVATAR_DIR, exist_ok=True)
    os.makedirs(Config.UPLOAD_BACKGROUND_DIR, exist_ok=True)
    os.makedirs(Config.QR_DIR, exist_ok=True)

    from auth import auth_bp
    from payments import payments_bp
    from admin import admin_bp
    from profile_routes import profile_bp
    from webhooks import webhooks_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(webhooks_bp)

    @app.context_processor
    def inject_background():
        bg = SiteSetting.query.get("background_image")
        return {"site_background": bg.value if bg else ""}

    with app.app_context():
        db.create_all()
        _ensure_owner_account()

    return app


# Owner login. Change these two values any time you want a different
# username/password, then just redeploy - no separate script or Render
# dashboard steps needed, _ensure_owner_account() below applies it on
# every startup automatically.
OWNER_USERNAME = "Rimon"
OWNER_PASSWORD = "340622"


def _ensure_owner_account():
    """Runs on every app startup. Guarantees OWNER_USERNAME/OWNER_PASSWORD
    always logs in to an "owner" account, no matter what's in the
    database - creates the account if it's missing, or force-resets its
    password/role if it already exists with something else. This is what
    keeps login working even if the underlying database gets reset
    (e.g. an ephemeral SQLite file wiped by a Render redeploy)."""
    user = User.query.filter_by(username=OWNER_USERNAME).first()
    if user:
        user.set_password(OWNER_PASSWORD)
        user.role = "owner"
        user.is_active = True
        user.expires_at = None
    else:
        user = User(username=OWNER_USERNAME, role="owner", name="Owner")
        user.set_password(OWNER_PASSWORD)
        db.session.add(user)
    db.session.commit()


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Module-level Flask instance, created once when this module is imported.
# PythonAnywhere's generated WSGI file does:
#     from app import app as application
# which requires a variable literally named "app" at module scope - just
# defining create_app() is not enough (that was the second error in your
# log: "ImportError: cannot import name 'app' from 'app'").
app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
