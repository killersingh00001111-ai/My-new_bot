"""Run once: `python add_new_owner.py`

Since the OTP email isn't arriving, this is the simplest fix: it creates a
SECOND owner account directly in the database with the exact username and
password you gave - no OTP, no email, nothing else needed. Your existing
owner account (whatever username/password it currently has) is NOT touched
at all - this only adds a new one alongside it.

After running this once, you can log in with either account:
  - your existing one (unchanged)
  - username: Rimon340   password: 837289
"""
from app import app  # noqa: also loads .env via config.py
from extensions import db
from models import User

NEW_USERNAME = "Rimon340"
NEW_PASSWORD = "837289"

with app.app_context():
    existing = User.query.filter_by(username=NEW_USERNAME).first()

    if existing:
        # Already there from a previous run - just make sure the password
        # matches what you asked for, in case it was run before with a
        # different value.
        existing.set_password(NEW_PASSWORD)
        db.session.commit()
        print(f"User '{NEW_USERNAME}' already existed - password reset to match this script.")
    else:
        user = User(username=NEW_USERNAME, role="owner", name="Owner")
        user.set_password(NEW_PASSWORD)
        db.session.add(user)
        db.session.commit()
        print(f"Created new owner account '{NEW_USERNAME}'.")

    print("Your original account is untouched. You can now log in with either account.")
