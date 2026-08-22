import uuid
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db


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
