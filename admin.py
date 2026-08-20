import os
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import User, SiteSetting
from decorators import owner_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@admin_bp.route("/")
@login_required
@owner_required
def panel():
    users = User.query.order_by(User.id).all()
    bg = SiteSetting.query.get("background_image")
    return render_template("admin_settings.html", users=users, background=bg.value if bg else "")


@admin_bp.route("/users/create", methods=["POST"])
@login_required
@owner_required
def create_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "admin")
    duration_raw = request.form.get("duration_days", "").strip()

    if role not in ("owner", "admin"):
        role = "admin"

    if not username or len(password) < 6:
        flash("Username required and password must be at least 6 characters.", "error")
        return redirect(url_for("admin.panel"))

    if User.query.filter_by(username=username).first():
        flash("That username already exists.", "error")
        return redirect(url_for("admin.panel"))

    # Duration only applies to admin accounts. Owner accounts never expire.
    # Leave it blank on the form for a permanent admin (no expiry).
    expires_at = None
    duration_days = None
    if role == "admin" and duration_raw:
        try:
            duration_days = int(duration_raw)
        except ValueError:
            duration_days = None
        if duration_days and duration_days > 0:
            expires_at = datetime.utcnow() + timedelta(days=duration_days)
        else:
            duration_days = None

    user = User(username=username, role=role, expires_at=expires_at, is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    # Build a ready-to-share "welcome" message with exactly what the new
    # admin needs: their login, and how long the access lasts. This is only
    # shown once, right after creation (the plaintext password is never
    # stored anywhere - only the hash is kept in the database from here on).
    if role == "admin":
        if duration_days:
            access_line = f"Your admin access is valid for {duration_days} day(s)."
        else:
            access_line = "Your admin access does not expire."
        welcome_text = (
            f"Welcome to Payment Admin, {username}!\n"
            f"You have been added as an admin.\n\n"
            f"Username: {username}\n"
            f"Password: {password}\n\n"
            f"{access_line}\n"
            f"Please log in and keep these details safe."
        )
    else:
        welcome_text = (
            f"Welcome to Payment Admin, {username}!\n"
            f"You have been added as an owner.\n\n"
            f"Username: {username}\n"
            f"Password: {password}\n\n"
            f"Please log in and keep these details safe."
        )

    flash(welcome_text, "welcome")
    flash(f"User {username} created.", "success")
    return redirect(url_for("admin.panel"))


@admin_bp.route("/users/<int:user_id>/disable", methods=["POST"])
@login_required
@owner_required
def disable_user(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You can't disable your own account.", "error")
        return redirect(url_for("admin.panel"))

    if user.is_owner:
        flash("Owner accounts can't be disabled from here.", "error")
        return redirect(url_for("admin.panel"))

    user.is_active = False
    db.session.commit()
    flash(f"{user.username} has been disabled and can no longer log in.", "success")
    return redirect(url_for("admin.panel"))


@admin_bp.route("/users/<int:user_id>/reactivate", methods=["POST"])
@login_required
@owner_required
def reactivate_user(user_id):
    user = User.query.get_or_404(user_id)
    new_password = request.form.get("new_password", "")

    if len(new_password) < 6:
        flash("New password must be at least 6 characters.", "error")
        return redirect(url_for("admin.panel"))

    user.set_password(new_password)
    user.is_active = True
    db.session.commit()

    welcome_text = (
        f"Welcome back to Payment Admin, {user.username}!\n"
        f"Your access has been restored.\n\n"
        f"Username: {user.username}\n"
        f"Password: {new_password}\n\n"
        f"Please log in and keep these details safe."
    )
    flash(welcome_text, "welcome")
    flash(f"{user.username} can log in again with the new password.", "success")
    return redirect(url_for("admin.panel"))


@admin_bp.route("/background", methods=["POST"])
@login_required
@owner_required
def change_background():
    bg_file = request.files.get("background")
    if not bg_file or not bg_file.filename or not _allowed(bg_file.filename):
        flash("Please choose a valid image file.", "error")
        return redirect(url_for("admin.panel"))

    ext = secure_filename(bg_file.filename).rsplit(".", 1)[1].lower()
    filename = f"bg_{uuid.uuid4().hex[:8]}.{ext}"
    bg_file.save(os.path.join(current_app.config["UPLOAD_BACKGROUND_DIR"], filename))

    setting = SiteSetting.query.get("background_image")
    if not setting:
        setting = SiteSetting(key="background_image")
        db.session.add(setting)
    setting.value = f"backgrounds/{filename}"
    db.session.commit()

    flash("Background updated.", "success")
    return redirect(url_for("admin.panel"))
