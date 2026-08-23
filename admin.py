import os
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import User, SiteSetting, parse_duration_to_seconds, format_duration_seconds
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
    duration_raw = request.form.get("duration", "").strip()

    if role not in ("owner", "admin"):
        role = "admin"

    if not username or len(password) < 6:
        flash("Username required and password must be at least 6 characters.", "error")
        return redirect(url_for("admin.panel"))

    if User.query.filter_by(username=username).first():
        flash("That username already exists.", "error")
        return redirect(url_for("admin.panel"))

    # Duration only applies to admin accounts. Owner accounts never expire.
    # Leave it blank on the form for a permanent admin (no expiry). This is
    # only stored here - it does NOT start counting down yet. expires_at
    # stays NULL until this admin's first successful login (see auth.py's
    # login()), so a brand-new account can sit unused for as long as
    # needed without any of its time ticking away.
    duration_seconds = None
    if role == "admin" and duration_raw:
        try:
            duration_seconds = parse_duration_to_seconds(duration_raw)
        except ValueError:
            flash(
                "Invalid duration - use a number followed by s/m/h/d, "
                "e.g. 45s, 10m, 3h, 5d.",
                "error",
            )
            return redirect(url_for("admin.panel"))

    user = User(username=username, role=role, is_active=True, duration_seconds=duration_seconds)
    user.set_password(password)
    # The password typed above is only a placeholder - this user hasn't
    # set a real password yet, so their first login will ask them to
    # create one (whatever they type there, min 8 chars, becomes it).
    user.must_set_password = True
    db.session.add(user)
    db.session.commit()

    # Build a ready-to-share "welcome" message with exactly what the new
    # admin needs: their login, and how long the access lasts. This is only
    # shown once, right after creation (the plaintext password is never
    # stored anywhere - only the hash is kept in the database from here on).
    if role == "admin":
        if duration_seconds:
            access_line = (
                f"Your admin access lasts {format_duration_seconds(duration_seconds)} "
                f"- that timer only starts once you log in for the first time, "
                f"not from right now."
            )
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
    # Same as a brand-new account: this new_password is just a
    # placeholder - on their next login they'll be asked to create their
    # own real password (min 8 chars) right there on the login page.
    user.must_set_password = True
    # Give them a fresh run at whatever duration they had (if any) - the
    # timer only starts again once they actually log in with this new
    # password, exactly like a brand-new admin account.
    user.first_login_at = None
    user.expires_at = None
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


@admin_bp.route("/reset-link")
@login_required
@owner_required
def reset_link():
    """Owner-only page showing the current "quick reset" link (if one has
    been generated), with copy/share actions - see quick_reset() in
    auth.py for what happens when someone opens that link."""
    setting = SiteSetting.query.get("quick_reset_token")
    token = setting.value if setting else ""
    reset_url = url_for("auth.quick_reset", token=token, _external=True) if token else ""
    return render_template("admin_reset_link.html", reset_url=reset_url)


@admin_bp.route("/reset-link/generate", methods=["POST"])
@login_required
@owner_required
def generate_reset_link():
    """(Re)generates the shared token used by the public quick-reset page.
    Generating a new one immediately invalidates any link shared before,
    since the old token no longer matches what's stored."""
    setting = SiteSetting.query.get("quick_reset_token")
    if not setting:
        setting = SiteSetting(key="quick_reset_token")
        db.session.add(setting)
    setting.value = uuid.uuid4().hex
    db.session.commit()
    flash("A new reset link has been generated. Any link shared before this no longer works.", "success")
    return redirect(url_for("admin.reset_link"))


@admin_bp.route("/reset-link/disable", methods=["POST"])
@login_required
@owner_required
def disable_reset_link():
    """Turns the quick-reset link off without deleting the setting row,
    so no token will ever match until a new one is generated."""
    setting = SiteSetting.query.get("quick_reset_token")
    if setting:
        setting.value = ""
        db.session.commit()
    flash("The reset link has been turned off.", "success")
    return redirect(url_for("admin.reset_link"))
