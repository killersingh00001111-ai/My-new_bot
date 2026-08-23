import uuid

from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from extensions import db
from models import ApiCredential, API_PLATFORMS, get_or_create_api_credentials

api_settings_bp = Blueprint("api_settings", __name__)


@api_settings_bp.route("/api-settings")
@login_required
def api_settings():
    """Shows the current user's own 3 integrations (Android APK, Website,
    Telegram Bot) - each with its own separate API ID + Callback ID,
    generated automatically the first time this page is opened. Every
    admin/owner only ever sees their own set here, never anyone else's."""
    credentials = get_or_create_api_credentials(current_user)
    ordered = [credentials[p] for p in API_PLATFORMS]
    return render_template("api_settings.html", credentials=ordered)


@api_settings_bp.route("/api-settings/<platform>/regenerate", methods=["POST"])
@login_required
def regenerate_api_credential(platform):
    """Replaces just this one platform's API ID + Callback ID with new
    random values - the other two platforms are left untouched. Whatever
    was set in that Android app / website / Telegram bot before will
    need to be updated with the new values."""
    if platform not in API_PLATFORMS:
        flash("Unknown platform.", "error")
        return redirect(url_for("api_settings.api_settings"))

    cred = ApiCredential.query.filter_by(user_id=current_user.id, platform=platform).first()
    if not cred:
        flash("That integration hasn't been set up yet.", "error")
        return redirect(url_for("api_settings.api_settings"))

    cred.api_id = uuid.uuid4().hex
    cred.callback_id = uuid.uuid4().hex
    db.session.commit()
    flash(
        f"New API ID and Callback ID generated for {cred.platform_label}. "
        f"Update it wherever it was set before - the old values stop working now.",
        "success",
    )
    return redirect(url_for("api_settings.api_settings"))
