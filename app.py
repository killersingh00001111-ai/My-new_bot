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

    # Keep-alive endpoint for external pingers (UptimeRobot, cron-job.org,
    # etc). Deliberately does nothing but return 200 OK - no auth, no
    # database query - so it's cheap to hit every 5-10 minutes and stops
    # Render's free plan from putting the service to sleep after 15 minutes
    # of no traffic.
    @app.route("/ping")
    def ping():
        return "OK", 200

    with app.app_context():
        db.create_all()
        _ensure_owner_account()

    return app


def _ensure_owner_account():
    """Create the initial owner account from OWNER_USERNAME / OWNER_PASSWORD
    if no owner exists yet. Runs automatically every time the app starts.

    This replaces having to run `python seed.py` by hand - useful on hosts
    like Render's free tier where Shell access needs a paid plan. It's safe
    to run on every restart/deploy: it only ever creates the very first
    owner, and does nothing once one already exists.
    """
    username = os.environ.get("OWNER_USERNAME")
    password = os.environ.get("OWNER_PASSWORD")
    name = os.environ.get("OWNER_NAME", "Owner")

    if not username or not password:
        return  # nothing to seed - OWNER_USERNAME/OWNER_PASSWORD not set

    if User.query.filter_by(role="owner").first():
        return  # an owner already exists, never overwrite it automatically

    if User.query.filter_by(username=username).first():
        return  # that username is taken by a non-owner user - do nothing

    owner = User(username=username, role="owner", name=name)
    owner.set_password(password)
    db.session.add(owner)
    db.session.commit()
    print(f"[startup] Created owner account '{username}'.")


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
