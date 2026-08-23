"""Public JSON API used by an admin's own Android app, website, or
Telegram bot to create and check payment links through this system -
separate from (and never touching) the panel's own /payments/create flow
in payments.py, which is left completely as-is.

Auth: every request needs the API ID for the SPECIFIC platform it's
calling as (android / website / telegram are three different API IDs,
even for the same admin) - sent either as an "X-Api-Id" header, an
"api_id" field in the JSON body / form, or an "api_id" query parameter.
See api_settings.py for where an admin gets these values.
"""
import os
import uuid
from datetime import datetime, timedelta

import qrcode
from flask import Blueprint, request, jsonify, current_app, url_for

from extensions import db
from models import ApiCredential, API_PLATFORMS, PaymentLink
from einpays_client import EinpaysClient, EinpaysError

external_api_bp = Blueprint("external_api", __name__, url_prefix="/api/v1")


def _authenticate(platform):
    """Looks up the ApiCredential for this exact platform matching the
    API ID sent with the request, and checks that the admin/owner who
    owns it can still actually log in (not disabled, not expired) -
    exactly the same rule as logging into the panel itself. Returns
    (credential, None) on success, or (None, (message, http_status)) on
    failure."""
    body = request.get_json(silent=True) or {}
    api_id = (
        request.headers.get("X-Api-Id")
        or request.args.get("api_id")
        or request.form.get("api_id")
        or body.get("api_id")
    )
    if not api_id:
        return None, (
            "Missing API ID - send it as the X-Api-Id header, or an api_id field/query param.",
            401,
        )

    cred = ApiCredential.query.filter_by(platform=platform, api_id=api_id).first()
    if not cred:
        return None, ("Invalid API ID for this platform.", 401)

    if not cred.user.can_login:
        return None, ("This admin's access has been disabled or has expired.", 403)

    return cred, None


@external_api_bp.route("/<platform>/create-payment", methods=["POST"])
def create_payment(platform):
    """Creates a payment link + QR code - same Einpays call and same
    PaymentLink row as the panel's own create-payment page, just
    reachable as JSON from outside. Optional "callback_url" in the
    request body gets a POST from webhooks.py once Einpays confirms the
    payment (see _send_external_callback in webhooks.py)."""
    if platform not in API_PLATFORMS:
        return jsonify({"error": f"Unknown platform '{platform}'. Use one of: {', '.join(API_PLATFORMS)}."}), 404

    cred, error = _authenticate(platform)
    if error:
        message, status = error
        return jsonify({"error": message}), status

    data = request.get_json(silent=True) or request.form or {}

    try:
        amount = int(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a whole number."}), 400

    if amount < PaymentLink.MIN_AMOUNT or amount > PaymentLink.MAX_AMOUNT:
        return jsonify(
            {"error": f"amount must be between {PaymentLink.MIN_AMOUNT} and {PaymentLink.MAX_AMOUNT}."}
        ), 400

    requested_method = data.get("requested_method") or "ANY"
    callback_url = (data.get("callback_url") or "").strip() or None

    try:
        expiry_minutes = int(data.get("expiry_minutes") or current_app.config["DEFAULT_LINK_EXPIRY_MINUTES"])
    except (TypeError, ValueError):
        expiry_minutes = current_app.config["DEFAULT_LINK_EXPIRY_MINUTES"]

    client_transaction_id = uuid.uuid4().hex
    try:
        client = EinpaysClient(current_app.config)
        result = client.create_deposit(
            amount=amount,
            client_transaction_id=client_transaction_id,
            requested_method=requested_method,
            # Same per-admin distinct id as the panel's own create_payment
            # in payments.py, so requests from this API never collide
            # with (or block) that admin's other pending transactions.
            client_user_id=f"user-{cred.user_id}",
        )
    except EinpaysError as exc:
        return jsonify({"error": f"Einpays error: {exc}"}), 502
    except Exception as exc:
        current_app.logger.exception("External API: create_deposit failed")
        return jsonify({"error": f"Could not reach Einpays: {exc}"}), 502

    payment_link_url = None
    available_methods = result.get("available_methods") or {}
    for method in available_methods.values():
        if method.get("payment_link"):
            payment_link_url = method["payment_link"]
            break

    if not payment_link_url:
        current_app.logger.warning("External API: Einpays create_deposit response had no link: %s", result)
        return jsonify({"error": "Einpays did not return a payment link."}), 502

    link = PaymentLink(
        client_transaction_id=client_transaction_id,
        einpays_transaction_id=result.get("transaction_id", ""),
        amount=amount,
        requested_method=requested_method,
        verification_mode="auto",
        payment_link=payment_link_url,
        status="CREATED",
        expires_at=datetime.utcnow() + timedelta(minutes=expiry_minutes),
        created_by_id=cred.user_id,
        source_platform=platform,
        callback_url=callback_url,
        api_credential_id=cred.id,
    )
    db.session.add(link)
    db.session.commit()

    qr_filename = f"{client_transaction_id}.png"
    qr_path = os.path.join(current_app.config["QR_DIR"], qr_filename)
    img = qrcode.make(payment_link_url)
    img.save(qr_path)
    link.qr_image_path = f"qrcodes/{qr_filename}"
    db.session.commit()

    return jsonify({
        "client_transaction_id": link.client_transaction_id,
        "transaction_id": link.einpays_transaction_id,
        "amount": link.amount,
        "status": link.status,
        "payment_link": link.payment_link,
        "qr_code_url": url_for("static", filename=link.qr_image_path, _external=True),
        "expires_at": link.expires_at.isoformat() + "Z",
    }), 201


@external_api_bp.route("/<platform>/payments/<client_transaction_id>", methods=["GET"])
def payment_status(platform, client_transaction_id):
    """Lets the Android app / website / Telegram bot poll a payment's
    current status by the client_transaction_id they got back from
    create-payment above (useful as a fallback if callback_url wasn't
    set, or just to double check)."""
    if platform not in API_PLATFORMS:
        return jsonify({"error": f"Unknown platform '{platform}'."}), 404

    cred, error = _authenticate(platform)
    if error:
        message, status = error
        return jsonify({"error": message}), status

    link = PaymentLink.query.filter_by(
        client_transaction_id=client_transaction_id,
        api_credential_id=cred.id,
    ).first()
    if not link:
        return jsonify({"error": "No payment found with that client_transaction_id for this API ID."}), 404

    return jsonify({
        "client_transaction_id": link.client_transaction_id,
        "transaction_id": link.einpays_transaction_id,
        "amount": link.amount,
        "status": link.status,
        "expires_at": link.expires_at.isoformat() + "Z",
    })
