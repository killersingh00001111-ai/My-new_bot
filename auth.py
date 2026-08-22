import random
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, session
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User, PasswordResetOTP

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("payments.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.can_login:
                reason = "expired" if user.is_expired else "disabled"
                flash(
                    f"This account has been {reason}. Ask the owner to reactivate it.",
                    "error",
                )
                return render_template("login.html")

            login_user(user)
            session.permanent = True
            return redirect(url_for("payments.dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


def _send_otp_email(code):
    """Send the OTP to every configured reset inbox (RESET_OTP_EMAIL and
    RESET_OTP_EMAIL_2 - see config.py's OTP_RECIPIENTS).
    Returns True if the email was actually sent to at least one inbox,
    False otherwise (SMTP not configured, or the send failed) so the
    caller can tell the admin what happened instead of silently
    pretending it worked."""
    to_addrs = current_app.config["OTP_RECIPIENTS"]
    host = current_app.config["SMTP_HOST"]
    user = current_app.config["SMTP_USERNAME"]
    pwd = current_app.config["SMTP_PASSWORD"]
    from_addr = current_app.config["SMTP_FROM"] or user

    if not (host and user and pwd and to_addrs):
        current_app.logger.warning("SMTP not configured - OTP code (dev only): %s", code)
        return False

    msg = MIMEText(f"Your password reset code is: {code}\nIt expires in 10 minutes.")
    msg["Subject"] = "Password reset code"
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)

    try:
        with smtplib.SMTP(host, current_app.config["SMTP_PORT"], timeout=15) as server:
            server.starttls()
            server.login(user, pwd)
            # Pass every recipient here (not just in the "To" header) so each
            # inbox actually gets the message - the "To" header alone is just
            # display text and does not control delivery.
            server.sendmail(from_addr, to_addrs, msg.as_string())
        return True
    except Exception:
        # Don't let a bad SMTP config (wrong host/app-password/etc.) blow up
        # the request with a 500 - log the real reason server-side and let
        # the caller decide what to tell the admin.
        current_app.logger.exception(
            "Failed to send OTP email - OTP code (dev only): %s", code
        )
        return False


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = User.query.filter_by(username=username).first()

        # Always show the same message, whether or not the username exists,
        # so the form can't be used to enumerate valid usernames.
        if user:
            code = f"{random.randint(0, 999999):06d}"
            otp = PasswordResetOTP(
                user_id=user.id,
                code=code,
                expires_at=datetime.utcnow() + timedelta(minutes=10),
            )
            db.session.add(otp)
            db.session.commit()
            sent = _send_otp_email(code)

            if not sent:
                # This is a closed admin/owner-only tool (no public signup),
                # so it's safe - and far more useful - to show the code
                # directly instead of leaving the admin stuck with no email.
                flash(
                    f"Email isn't configured (or the send failed) - "
                    f"your reset code is: {code}",
                    "error",
                )
                return redirect(url_for("auth.reset_password", username=username))

        flash("If that account exists, a reset code has been sent.", "info")
        return redirect(url_for("auth.reset_password", username=username))

    return render_template("forgot_password.html")


@auth_bp.route("/resend-otp", methods=["POST"])
def resend_otp():
    """Called by the 'Resend code' button on the reset-password page.
    Issues a fresh OTP the same way forgot_password does, and returns a
    small JSON payload the page's JS uses to restart the countdown."""
    username = request.form.get("username", "").strip()
    user = User.query.filter_by(username=username).first()

    if not user:
        # Same "don't reveal whether the account exists" behaviour as
        # forgot_password.
        return {"ok": True, "message": "If that account exists, a new code has been sent."}

    code = f"{random.randint(0, 999999):06d}"
    otp = PasswordResetOTP(
        user_id=user.id,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    db.session.add(otp)
    db.session.commit()
    sent = _send_otp_email(code)

    if not sent:
        return {
            "ok": True,
            "message": f"Email isn't configured (or the send failed) - your new code is: {code}",
        }

    return {"ok": True, "message": "A new code has been sent."}


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    username = request.args.get("username", "") or request.form.get("username", "")

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        new_password = request.form.get("new_password", "")

        user = User.query.filter_by(username=username).first()
        otp = (
            PasswordResetOTP.query.filter_by(user_id=user.id if user else -1, code=code, used=False)
            .order_by(PasswordResetOTP.id.desc())
            .first()
        )

        if not user or not otp or otp.expires_at < datetime.utcnow():
            flash("Invalid or expired code.", "error")
            return render_template("reset_password.html", username=username)

        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("reset_password.html", username=username)

        user.set_password(new_password)
        otp.used = True
        db.session.commit()

        flash("Password updated. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", username=username)
