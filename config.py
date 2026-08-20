import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env from this file's own folder, not from whatever the current
# working directory happens to be (PythonAnywhere runs the WSGI process
# from a different cwd, which silently made .env not get picked up).
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")

    _db_url = os.environ.get("DATABASE_URL", "sqlite:///app.db")
    # Render/Supabase/Heroku-style providers hand out "postgres://" URLs, but
    # SQLAlchemy 1.4+ requires "postgresql://" - rewrite it automatically so
    # switching DATABASE_URL to a hosted Postgres just works.
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---- Password reset ----
    # Primary inbox that receives the OTP for ANY account's "forgot password" flow.
    RESET_OTP_EMAIL = os.environ.get("RESET_OTP_EMAIL", "")
    # Second inbox that also receives every OTP. Defaults to the address you asked
    # to add; override or clear it in .env if you ever want to change/remove it.
    RESET_OTP_EMAIL_2 = os.environ.get("RESET_OTP_EMAIL_2", "rimon200n@gmail.com")
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")

    # ---- Einpays ----
    EINPAYS_BASE_URL = os.environ.get("EINPAYS_BASE_URL", "https://pay18.einpays.com")
    EINPAYS_CLIENT_ID = os.environ.get("EINPAYS_CLIENT_ID", "528")
    EINPAYS_COUNTRY_ID = os.environ.get("EINPAYS_COUNTRY_ID", "1")
    EINPAYS_CURRENCY_ID = os.environ.get("EINPAYS_CURRENCY_ID", "3")

    # Confirmed from the Einpays "Common Deposits API" PDF: the deposit
    # (payin) creation call IS the "/api/v5/methods/get" endpoint - despite
    # the name, that single endpoint is what accepts the create-deposit
    # JWT request and returns the payment_link. There is no separate
    # "list methods" endpoint documented, so EINPAYS_METHODS_ENDPOINT below
    # is kept only as an alias pointing at the same path.
    EINPAYS_CREATE_DEPOSIT_ENDPOINT = os.environ.get(
        "EINPAYS_CREATE_DEPOSIT_ENDPOINT", "/api/v5/methods/get"
    )
    EINPAYS_METHODS_ENDPOINT = os.environ.get("EINPAYS_METHODS_ENDPOINT", "/api/v5/methods/get")
    EINPAYS_BALANCE_ENDPOINT = os.environ.get("EINPAYS_BALANCE_ENDPOINT", "/api/v5/balance")
    EINPAYS_TXSTATUS_ENDPOINT = os.environ.get("EINPAYS_TXSTATUS_ENDPOINT", "/api/v5/txstatus")
    EINPAYS_PAYOUT_GETFORM_ENDPOINT = os.environ.get(
        "EINPAYS_PAYOUT_GETFORM_ENDPOINT", "/api/v5/payouts/getform"
    )
    EINPAYS_PAYOUT_SUBMIT_ENDPOINT = os.environ.get(
        "EINPAYS_PAYOUT_SUBMIT_ENDPOINT", "/api/v5/payouts/submit"
    )

    PRIVATE_KEY_PATH = os.path.join(BASE_DIR, "keys", "private.pem")
    PUBLIC_KEY_PATH = os.path.join(BASE_DIR, "keys", "public.pem")
    EINPAYS_RESPONSE_PUBLIC_KEY_PATH = os.path.join(
        BASE_DIR, "keys", "einpays_response_public.pem"
    )
    EINPAYS_CALLBACK_PUBLIC_KEY_PATH = os.path.join(
        BASE_DIR, "keys", "einpays_callback_public.pem"
    )

    DEFAULT_LINK_EXPIRY_MINUTES = int(os.environ.get("DEFAULT_LINK_EXPIRY_MINUTES", "10"))

    # Every inbox that should receive password-reset OTP codes, with blanks and
    # duplicates removed (in case RESET_OTP_EMAIL and RESET_OTP_EMAIL_2 are ever
    # set to the same address). Computed once here, NOT as a @property, because
    # Config is passed to Flask as the class itself (app.config.from_object(Config)),
    # and a @property on a class only evaluates correctly on an *instance*.
    _otp_addrs = [RESET_OTP_EMAIL, RESET_OTP_EMAIL_2]
    OTP_RECIPIENTS = []
    for _addr in _otp_addrs:
        _addr = (_addr or "").strip()
        if _addr and _addr not in OTP_RECIPIENTS:
            OTP_RECIPIENTS.append(_addr)
    del _otp_addrs, _addr

    UPLOAD_AVATAR_DIR = os.path.join(BASE_DIR, "static", "avatars")
    UPLOAD_BACKGROUND_DIR = os.path.join(BASE_DIR, "static", "backgrounds")
    QR_DIR = os.path.join(BASE_DIR, "static", "qrcodes")
