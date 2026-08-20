import os
import uuid
from datetime import datetime, timedelta

import qrcode
from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required, current_user

from extensions import db
from models import PaymentLink
from einpays_client import EinpaysClient, EinpaysError

payments_bp = Blueprint("payments", __name__)


@payments_bp.route("/")
@login_required
def dashboard():
    links = PaymentLink.query.order_by(PaymentLink.created_at.desc()).limit(50).all()
    return render_template("dashboard.html", links=links)


@payments_bp.route("/payments/create", methods=["GET", "POST"])
@login_required
def create_payment():
    if request.method == "POST":
        try:
            amount = int(request.form.get("amount", "0"))
        except ValueError:
            flash("Amount must be a whole number.", "error")
            return render_template("create_payment.html")

        if amount <= 0:
            flash("Amount must be greater than 0.", "error")
            return render_template("create_payment.html")

        expiry_minutes = int(
            request.form.get("expiry_minutes") or current_app.config["DEFAULT_LINK_EXPIRY_MINUTES"]
        )
        requested_method = request.form.get("requested_method", "ANY")

        client_transaction_id = uuid.uuid4().hex
        client = EinpaysClient(current_app.config)

        try:
            result = client.create_deposit(
                amount=amount,
                client_transaction_id=client_transaction_id,
                requested_method=requested_method,
            )
        except EinpaysError as exc:
            flash(f"Einpays error: {exc}", "error")
            return render_template("create_payment.html")
        except Exception as exc:  # network / HTTP errors
            flash(f"Could not reach Einpays: {exc}", "error")
            return render_template("create_payment.html")

        # Response shape follows the "available_methods" example in the docs:
        # payload.available_methods.<n>.payment_link, plus payload.transaction_id
        payment_link_url = None
        available_methods = result.get("available_methods") or {}
        for method in available_methods.values():
            if method.get("payment_link"):
                payment_link_url = method["payment_link"]
                break

        if not payment_link_url:
            flash("Einpays did not return a payment link. Check the raw response in logs.", "error")
            current_app.logger.warning("Einpays create_deposit response: %s", result)
            return render_template("create_payment.html")

        link = PaymentLink(
            client_transaction_id=client_transaction_id,
            einpays_transaction_id=result.get("transaction_id", ""),
            amount=amount,
            requested_method=requested_method,
            payment_link=payment_link_url,
            status="CREATED",
            expires_at=datetime.utcnow() + timedelta(minutes=expiry_minutes),
            created_by_id=current_user.id,
        )
        db.session.add(link)
        db.session.commit()

        # Generate a QR code image for the payment link
        qr_filename = f"{client_transaction_id}.png"
        qr_path = os.path.join(current_app.config["QR_DIR"], qr_filename)
        img = qrcode.make(payment_link_url)
        img.save(qr_path)
        link.qr_image_path = f"qrcodes/{qr_filename}"
        db.session.commit()

        return redirect(url_for("payments.payment_detail", link_id=link.id))

    return render_template("create_payment.html")


@payments_bp.route("/payments/<int:link_id>")
@login_required
def payment_detail(link_id):
    link = PaymentLink.query.get_or_404(link_id)
    return render_template("payment_detail.html", link=link)
