"""
Thin client for the Einpays payin/payout API.

Every request body and response body is a JWT (RS256):
  - We sign OUTGOING requests with OUR private key, and include our public
    key (PEM text) inside the payload as "client_pub_key" (per their docs).
  - We verify INCOMING responses with EINPAYS' "API messages" public key.
  - We verify INCOMING callbacks (webhooks) with EINPAYS' "Callbacks" public key.

NOTE: per the Einpays "Common Deposits API" PDF, the create-deposit request
is sent to the "/api/v5/methods/get" endpoint - that is the actual
create-deposit call, not a separate "list methods" endpoint. This is
confirmed in config.py (EINPAYS_CREATE_DEPOSIT_ENDPOINT default).
"""
import time
import uuid
import hashlib
import secrets

import jwt
import requests


def _read_key(path):
    with open(path, "r") as f:
        return f.read()


class EinpaysError(Exception):
    pass


class EinpaysClient:
    def __init__(self, config):
        # `config` is Flask's current_app.config (a dict-like object), NOT
        # the plain `Config` class from config.py - it only supports
        # dictionary-style access (config["KEY"]), not attribute access
        # (config.KEY). Using attribute access here was the cause of:
        #   AttributeError: 'Config' object has no attribute 'EINPAYS_BASE_URL'
        self.base_url = config["EINPAYS_BASE_URL"].rstrip("/")
        self.client_id = config["EINPAYS_CLIENT_ID"]
        self.country_id = config["EINPAYS_COUNTRY_ID"]
        self.currency_id = config["EINPAYS_CURRENCY_ID"]

        self.private_key = _read_key(config["PRIVATE_KEY_PATH"])
        self.public_key_pem = _read_key(config["PUBLIC_KEY_PATH"])
        self.einpays_response_public_key = _read_key(config["EINPAYS_RESPONSE_PUBLIC_KEY_PATH"])
        self.einpays_callback_public_key = _read_key(config["EINPAYS_CALLBACK_PUBLIC_KEY_PATH"])

        self.create_deposit_endpoint = config["EINPAYS_CREATE_DEPOSIT_ENDPOINT"]
        self.methods_endpoint = config["EINPAYS_METHODS_ENDPOINT"]
        self.balance_endpoint = config["EINPAYS_BALANCE_ENDPOINT"]
        self.txstatus_endpoint = config["EINPAYS_TXSTATUS_ENDPOINT"]
        self.payout_getform_endpoint = config["EINPAYS_PAYOUT_GETFORM_ENDPOINT"]
        self.payout_submit_endpoint = config["EINPAYS_PAYOUT_SUBMIT_ENDPOINT"]

    def _salt(self):
        return hashlib.sha256(secrets.token_bytes(32)).hexdigest()

    def _sign_payload(self, payload: dict) -> str:
        return jwt.encode({"payload": payload}, self.private_key, algorithm="RS256")

    def _post(self, endpoint, payload: dict):
        token = self._sign_payload(payload)
        url = f"{self.base_url}{endpoint}"
        resp = requests.post(url, data=token, headers={"Content-Type": "text/plain"}, timeout=30)
        resp.raise_for_status()
        return self._decode_response(resp.text)

    def _decode_response(self, raw_jwt_text: str) -> dict:
        try:
            decoded = jwt.decode(
                raw_jwt_text.strip(),
                self.einpays_response_public_key,
                algorithms=["RS256"],
            )
        except jwt.PyJWTError as exc:
            raise EinpaysError(f"Could not verify Einpays response signature: {exc}")
        return decoded.get("payload", decoded)

    def verify_callback(self, raw_jwt_text: str) -> dict:
        """Verify and decode a webhook/callback JWT sent by Einpays."""
        try:
            decoded = jwt.decode(
                raw_jwt_text.strip(),
                self.einpays_callback_public_key,
                algorithms=["RS256"],
            )
        except jwt.PyJWTError as exc:
            raise EinpaysError(f"Could not verify Einpays callback signature: {exc}")
        return decoded.get("payload", decoded)

    # ---- Deposits (payin) ----
    def create_deposit(self, amount, client_transaction_id, requested_method="ANY", client_user_id="MERCHANT"):
        payload = {
            "salt": self._salt(),
            "timestamp": str(int(time.time())),
            "client_id": self.client_id,
            "transaction_type": "1",  # 1 = Deposit
            "requested_method": requested_method,
            "country_id": self.country_id,
            "currency_id": self.currency_id,
            "amount": amount,
            "client_user_id": client_user_id,
            "client_user_ipaddr": "0.0.0.0",
            "client_transaction_id": client_transaction_id,
            "client_pub_key": self.public_key_pem,
        }
        return self._post(self.create_deposit_endpoint, payload)

    # ---- Balance ----
    def get_balance(self):
        payload = {
            "salt": self._salt(),
            "timestamp": str(int(time.time())),
            "client_id": self.client_id,
            "client_pub_key": self.public_key_pem,
        }
        return self._post(self.balance_endpoint, payload)

    # ---- Transaction status ----
    def get_transaction_status(self, order_ids):
        payload = {
            "salt": self._salt(),
            "timestamp": str(int(time.time())),
            "client_id": self.client_id,
            "orders": order_ids,
            "client_pub_key": self.public_key_pem,
        }
        return self._post(self.txstatus_endpoint, payload)

    # ---- Payouts (withdrawals) ----
    def get_payout_form(self, amount, client_transaction_id, requested_method="IMPS", client_user_id="MERCHANT"):
        payload = {
            "salt": self._salt(),
            "timestamp": str(int(time.time())),
            "client_id": self.client_id,
            "transaction_type": "2",  # 2 = Payout
            "requested_method": requested_method,
            "country_id": self.country_id,
            "currency_id": self.currency_id,
            "amount": amount,
            "client_user_id": client_user_id,
            "client_user_ipaddr": "0.0.0.0",
            "client_transaction_id": client_transaction_id,
            "client_pub_key": self.public_key_pem,
        }
        return self._post(self.payout_getform_endpoint, payload)

    def submit_payout(self, request_id, submitted_information: dict):
        payload = {
            "salt": self._salt(),
            "timestamp": str(int(time.time())),
            "request_id": request_id,
            "submitted_information": submitted_information,
            "client_pub_key": self.public_key_pem,
        }
        return self._post(self.payout_submit_endpoint, payload)
