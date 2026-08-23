import re
import uuid
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db

# Accepts a whole number followed by one unit letter: s (seconds),
# m (minutes), h (hours), d (days) - e.g. "45s", "10m", "3h", "5d".
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_DURATION_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_DURATION_UNIT_NAMES = {"s": "second", "m": "minute", "h": "hour", "d": "day"}


def parse_duration_to_seconds(raw):
    """Turn a duration string like "45s"/"10m"/"3h"/"5d" into whole
    seconds. Returns None for a blank/empty string (meaning "no expiry").
    Raises ValueError if raw is non-blank but doesn't match the expected
    "<number><unit>" shape."""
    raw = (raw or "").strip()
    if not raw:
        return None

    match = _DURATION_RE.match(raw)
    if not match:
        raise ValueError(f"Invalid duration format: {raw!r}")

    value = int(match.group(1))
    unit = match.group(2).lower()
    if value <= 0:
        raise ValueError("Duration must be a positive number.")

    return value * _DURATION_UNIT_SECONDS[unit]


def format_duration_seconds(seconds):
    """Human-readable form of a duration_seconds value, e.g. 10800 -> "3
    hours". Picks the largest unit that divides the value evenly (which
    is always the original unit that was typed in, since we only ever
    store exact multiples of one unit)."""
    if not seconds:
        return None
    seconds = int(seconds)

    for unit in ("d", "h", "m"):
        size = _DURATION_UNIT_SECONDS[unit]
        if seconds >= size and seconds % size == 0:
            n = seconds // size
            name = _DURATION_UNIT_NAMES[unit]
            return f"{n} {name}{'s' if n != 1 else ''}"

    name = _DURATION_UNIT_NAMES["s"]
    return f"{seconds} {name}{'s' if seconds != 1 else ''}"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    # "owner" or "admin" - only these two roles exist, no public signup.
    role = db.Column(db.String(20), nullable=False, default="admin")

    name = db.Column(db.String(120), default="")
    bio = db.Column(db.Text, default="")
    avatar_path = db.Column(db.String(255), default="")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Access control for admin accounts created from the Admin Panel.
    # - is_active: set to False when the owner "deletes" (disables) the
    #   account. The row is kept (so past payment links keep their
    #   created_by reference) but the user can no longer log in until the
    #   owner reactivates them with a new password.
    # - expires_at: optional time-boxed access ("admin for N days"). NULL
    #   means no expiry. Only set for admins created through the new
    #   "Add user" form with a duration - existing/owner accounts are left
    #   as NULL (never expire) so this never changes accounts that already
    #   existed before this feature.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)

    # The requested lifetime for a time-boxed admin, in seconds (parsed
    # from things like "45s"/"10m"/"3h"/"5d" - see parse_duration_to_seconds
    # above). This is stored right away when the owner creates the account,
    # but it does NOT start counting down yet - expires_at above stays NULL
    # until this admin's first successful login, at which point auth.py
    # stamps first_login_at and sets expires_at = first_login_at +
    # duration_seconds. So an admin account can sit unused for any length
    # of time with no risk of expiring before it's ever been used.
    duration_seconds = db.Column(db.Integer, nullable=True)

    # When this account was first successfully logged into. NULL means
    # "never logged in yet" - which is also what keeps expires_at NULL
    # (see duration_seconds above). Resetting this user's password later
    # (via the owner's reactivate flow, the OTP flow, or the quick-reset
    # link) never touches this or expires_at - only a fresh account or an
    # explicit reactivation starts the clock over.
    first_login_at = db.Column(db.DateTime, nullable=True)

    # When True, this account has no real password yet - whatever the
    # owner/admin typed into the "password" field when creating or
    # reactivating this user is just a placeholder. The NEXT successful
    # login attempt (matching username, any password >= 8 chars) sets
    # THAT typed value as the real password and clears this flag - the
    # login page itself doubles as the "create your password" step, no
    # separate screen. Stays False for accounts that already went through
    # this once, until the owner reactivates them again (new access =
    # must set a new password again). Existing accounts from before this
    # feature default to False so nothing changes for them.
    must_set_password = db.Column(db.Boolean, default=False, nullable=False)

    # Short numeric PIN (like a phone's app-lock PIN) used to unlock the
    # site again after it's been left/backgrounded - separate from the
    # main username/password, which is only needed for the actual login.
    pin_hash = db.Column(db.String(255), nullable=True)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def set_pin(self, raw_pin):
        self.pin_hash = generate_password_hash(raw_pin)

    def check_pin(self, raw_pin):
        return bool(self.pin_hash) and check_password_hash(self.pin_hash, raw_pin)

    @property
    def is_owner(self):
        return self.role == "owner"

    @property
    def is_expired(self):
        """True if this account had a time-boxed duration and it has passed."""
        return bool(self.expires_at and datetime.utcnow() > self.expires_at)

    @property
    def can_login(self):
        """False if the account was disabled by the owner, or its
        admin-duration has expired."""
        return self.is_active and not self.is_expired

    @property
    def formatted_duration(self):
        """Human-readable version of duration_seconds, e.g. "3 hours", or
        None if this account has no time-boxed duration at all."""
        return format_duration_seconds(self.duration_seconds)


class PasswordResetOTP(db.Model):
    __tablename__ = "password_reset_otps"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)

    user = db.relationship("User")


class PaymentLink(db.Model):
    __tablename__ = "payment_links"

    # Business rule: every payment link must be for at least this much and
    # at most this much (in the currency's smallest whole unit, e.g. Rupees).
    MIN_AMOUNT = 300
    MAX_AMOUNT = 100000

    id = db.Column(db.Integer, primary_key=True)
    client_transaction_id = db.Column(db.String(64), unique=True, default=lambda: uuid.uuid4().hex)
    einpays_transaction_id = db.Column(db.String(120))

    amount = db.Column(db.Integer, nullable=False)  # amount in smallest currency unit, per Einpays docs
    requested_method = db.Column(db.String(40), default="ANY")

    # How this link's success is confirmed:
    #  - "auto":   Einpays calls our /webhooks/einpays callback automatically
    #              when the payment finishes, and we flip status ourselves.
    #  - "manual": the payer/merchant enters the Einpays transaction ID by
    #              hand on the payment page, and we look it up via the
    #              txstatus API to confirm and mark it Approved.
    verification_mode = db.Column(db.String(10), default="auto", nullable=False)

    payment_link = db.Column(db.String(500))
    qr_image_path = db.Column(db.String(255))

    status = db.Column(db.String(20), default="CREATED")  # CREATED / PENDING / APPROVED / REJECTED / EXPIRED
    expires_at = db.Column(db.DateTime, nullable=False)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User")

    @property
    def is_expired(self):
        return datetime.utcnow() > self.expires_at and self.status in ("CREATED", "PENDING")


class SiteSetting(db.Model):
    __tablename__ = "site_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, default="")
