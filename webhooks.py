import requests
from flask import Blueprint, request, current_app, jsonify

from extensions import db
from models import PaymentLink
from einpays_client import EinpaysClient, EinpaysError

webhooks_bp = Blueprint("webhooks", __name__)


@webhooks_bp.route("/webhooks/einpays", methods=["POST"])
def einpays_callback():
    """Einpays POSTs a JWT (RS256) body here whenever a transaction reaches a
    final status (Approved / Rejected). Verify the signature before trusting
    anything in the payload."""
    raw_body = request.get_data(as_text=True)

    client = EinpaysClient(current_app.config)
    try:
        payload = client.verify_callback(raw_body)
    except EinpaysError as exc:
        current_app.logger.warning("Rejected callback with bad signature: %s", exc)
        return jsonify({"error": "invalid signature"}), 400

    client_transaction_id = payload.get("client_transaction_id")
    status = payload.get("transaction_status")
    transaction_id = payload.get("transaction_id")

    link = PaymentLink.query.filter_by(client_transaction_id=client_transaction_id).first()
    if link:
        link.status = status or link.status
        if transaction_id:
            link.einpays_transaction_id = transaction_id
        db.session.commit()

        # Additive: if this link was created through the external API
        # (an admin's Android app / website / Telegram bot - see
        # external_api.py) and a callback_url was given at creation
        # time, let that system know the status changed too, instead of
        # making it poll. Doesn't change anything above - if this fails
        # for any reason, the Einpays callback itself still succeeds.
        if link.callback_url:
            _send_external_callback(link)
    else:
        current_app.logger.warning(
            "Callback for unknown client_transaction_id=%s", client_transaction_id
        )

    # Respond 200 so Einpays marks the callback as delivered.
    return jsonify({"received": True}), 200


def _send_external_callback(link):
    """Best-effort POST to the callback_url an admin's Android app /
    website / Telegram bot supplied when it created this payment through
    the external API - lets them know the status changed without having
    to poll for it. callback_id is included so they can verify the
    notification really came from here (see api_settings.py for where
    that value comes from)."""
    callback_id = link.api_credential.callback_id if link.api_credential else None
    try:
        requests.post(
            link.callback_url,
            json={
                "callback_id": callback_id,
                "client_transaction_id": link.client_transaction_id,
                "transaction_id": link.einpays_transaction_id,
                "amount": link.amount,
                "status": link.status,
            },
            timeout=10,
        )
    except Exception:
        current_app.logger.exception(
            "Failed to deliver external callback for client_transaction_id=%s to %s",
            link.client_transaction_id,
            link.callback_url,
        )
