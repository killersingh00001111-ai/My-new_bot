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

    return app


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
