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
    else:
        current_app.logger.warning(
            "Callback for unknown client_transaction_id=%s", client_transaction_id
        )

    # Respond 200 so Einpays marks the callback as delivered.
    return jsonify({"received": True}), 200
