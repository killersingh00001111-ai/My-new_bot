import os
import uuid

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import PaymentLink

profile_bp = Blueprint("profile", __name__)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _payment_stats(user):
    """Summary of this user's own payment links, split by status, for the
    stats box shown on the profile page."""
    links = (
        PaymentLink.query.filter_by(created_by_id=user.id)
        .order_by(PaymentLink.created_at.desc())
        .all()
    )

    stats = {
        "success_count": 0, "success_amount": 0,
        "pending_count": 0, "pending_amount": 0,
        "expired_count": 0, "expired_amount": 0,
        "failed_count": 0, "failed_amount": 0,
        "total_count": len(links),
    }

    for link in links:
        if link.status == "APPROVED":
            stats["success_count"] += 1
            stats["success_amount"] += link.amount
        elif link.status == "REJECTED":
            stats["failed_count"] += 1
            stats["failed_amount"] += link.amount
        elif link.status == "EXPIRED" or link.is_expired:
            stats["expired_count"] += 1
            stats["expired_amount"] += link.amount
        else:  # CREATED / PENDING and not yet expired
            stats["pending_count"] += 1
            stats["pending_amount"] += link.amount

    # Most recent links for the "recent activity" list under the stats.
    stats["recent_links"] = links[:10]
    return stats


@profile_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.name = request.form.get("name", "").strip()
        current_user.bio = request.form.get("bio", "").strip()

        avatar_file = request.files.get("avatar")
        if avatar_file and avatar_file.filename and _allowed(avatar_file.filename):
            ext = secure_filename(avatar_file.filename).rsplit(".", 1)[1].lower()
            filename = f"user{current_user.id}_{uuid.uuid4().hex[:8]}.{ext}"
            avatar_file.save(os.path.join(current_app.config["UPLOAD_AVATAR_DIR"], filename))
            current_user.avatar_path = f"avatars/{filename}"

        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile.profile"))

    stats = _payment_stats(current_user)
    return render_template("profile.html", stats=stats)
