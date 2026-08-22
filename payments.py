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
            return render_template(
                "create_payment.html",
                min_amount=PaymentLink.MIN_AMOUNT,
                max_amount=PaymentLink.MAX_AMOUNT,
            )

        if amount < PaymentLink.MIN_AMOUNT:
            flash(f"Amount must be at least ₹{PaymentLink.MIN_AMOUNT}.", "error")
            return render_template(
                "create_payment.html",
                min_amount=PaymentLink.MIN_AMOUNT,
                max_amount=PaymentLink.MAX_AMOUNT,
            )

        if amount > PaymentLink.MAX_AMOUNT:
            flash(f"Amount cannot be more than ₹{PaymentLink.MAX_AMOUNT}.", "error")
            return render_template(
                "create_payment.html",
                min_amount=PaymentLink.MIN_AMOUNT,
                max_amount=PaymentLink.MAX_AMOUNT,
            )

        expiry_minutes = int(
            request.form.get("expiry_minutes") or current_app.config["DEFAULT_LINK_EXPIRY_MINUTES"]
        )
        requested_method = request.form.get("requested_method", "ANY")
        verification_mode = request.form.get("verification_mode", "auto")
        if verification_mode not in ("auto", "manual"):
            verification_mode = "auto"

        client_transaction_id = uuid.uuid4().hex
        try:
            client = EinpaysClient(current_app.config)
        except Exception as exc:
            flash(f"Payment gateway is not configured correctly: {exc}", "error")
            current_app.logger.exception("Failed to build EinpaysClient")
            return render_template(
                "create_payment.html",
                min_amount=PaymentLink.MIN_AMOUNT,
                max_amount=PaymentLink.MAX_AMOUNT,
            )

        try:
            result = client.create_deposit(
                amount=amount,
                client_transaction_id=client_transaction_id,
                requested_method=requested_method,
            )
        except EinpaysError as exc:
            flash(f"Einpays error: {exc}", "error")
            return render_template(
                "create_payment.html",
                min_amount=PaymentLink.MIN_AMOUNT,
                max_amount=PaymentLink.MAX_AMOUNT,
            )
        except Exception as exc:  # network / HTTP errors
            flash(f"Could not reach Einpays: {exc}", "error")
            return render_template(
                "create_payment.html",
                min_amount=PaymentLink.MIN_AMOUNT,
                max_amount=PaymentLink.MAX_AMOUNT,
            )

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
            return render_template(
                "create_payment.html",
                min_amount=PaymentLink.MIN_AMOUNT,
                max_amount=PaymentLink.MAX_AMOUNT,
            )

        link = PaymentLink(
            client_transaction_id=client_transaction_id,
            einpays_transaction_id=result.get("transaction_id", ""),
            amount=amount,
            requested_method=requested_method,
            verification_mode=verification_mode,
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

    return render_template(
        "create_payment.html",
        min_amount=PaymentLink.MIN_AMOUNT,
        max_amount=PaymentLink.MAX_AMOUNT,
    )


@payments_bp.route("/payments/<int:link_id>")
@login_required
def payment_detail(link_id):
    link = PaymentLink.query.get_or_404(link_id)
    return render_template("payment_detail.html", link=link)


@payments_bp.route("/payments/<int:link_id>/confirm-manual", methods=["POST"])
@login_required
def confirm_manual(link_id):
    """Used only for links created with verification_mode="manual": the
    merchant types in the Transaction ID the payer received from Einpays,
    and we ask Einpays' txstatus API to confirm it before marking the
    link Approved - so this can't just be typed in blind."""
    link = PaymentLink.query.get_or_404(link_id)

    if link.verification_mode != "manual":
        flash("This payment link is set to automatic confirmation.", "error")
        return redirect(url_for("payments.payment_detail", link_id=link.id))

    entered_transaction_id = request.form.get("transaction_id", "").strip()
    if not entered_transaction_id:
        flash("Please enter the Transaction ID.", "error")
        return redirect(url_for("payments.payment_detail", link_id=link.id))

    try:
        client = EinpaysClient(current_app.config)
        result = client.get_transaction_status([entered_transaction_id])
    except Exception as exc:
        flash(f"Could not verify that Transaction ID with Einpays: {exc}", "error")
        current_app.logger.exception("txstatus lookup failed")
        return redirect(url_for("payments.payment_detail", link_id=link.id))

    # The exact shape of Einpays' txstatus response isn't nailed down in the
    # docs we have - handle it defensively whether it comes back as a list
    # of order records or a dict keyed by transaction id.
    orders = result.get("orders", result) if isinstance(result, dict) else result
    record = None
    if isinstance(orders, dict):
        record = orders.get(entered_transaction_id)
    elif isinstance(orders, list):
        for item in orders:
            if str(item.get("transaction_id")) == entered_transaction_id or str(
                item.get("client_transaction_id")
            ) == entered_transaction_id:
                record = item
                break

    if not record:
        flash("Einpays has no record of that Transaction ID. Please double check it.", "error")
        return redirect(url_for("payments.payment_detail", link_id=link.id))

    status = (record.get("transaction_status") or record.get("status") or "").upper()
    if status in ("APPROVED", "SUCCESS", "SUCCESSFUL"):
        link.status = "APPROVED"
        link.einpays_transaction_id = entered_transaction_id
        db.session.commit()
        flash("Payment confirmed and marked Approved.", "success")
    else:
        flash(f"Einpays reports this transaction as '{status or 'unknown'}', not approved yet.", "error")

    return redirect(url_for("payments.payment_detail", link_id=link.id))
