"""Run once, any time your login stops working: `python reset_owner_password.py`

This version does NOT depend on the .env / Render Environment Variables
(OWNER_USERNAME / OWNER_PASSWORD) matching anything - the username and
password below are hardcoded directly in this file, so it will work even
if those environment variables are missing, blank, or wrong on Render.

It force-creates (or force-resets, if the account already exists) an
"owner" account with the exact username/password below. After running
this once, log in with:

    Username: Rimon1340
    Password: 629791

You can change NEW_USERNAME / NEW_PASSWORD below any time you want a
different login, then just run this file again.
"""

from app import app  # noqa: also loads .env via config.py
from extensions import db
from models import User

# ---- Change these any time you want a different owner login ----
NEW_USERNAME = "Rimon1340"
NEW_PASSWORD = "629791"
NEW_NAME = "Owner"
# ------------------------------------------------------------------

with app.app_context():
    user = User.query.filter_by(username=NEW_USERNAME).first()

    if user:
        user.set_password(NEW_PASSWORD)
        user.role = "owner"
        user.is_active = True
        user.expires_at = None
        db.session.commit()
        print(f"Password for existing user '{NEW_USERNAME}' has been force-reset.")
    else:
        user = User(username=NEW_USERNAME, role="owner", name=NEW_NAME)
        user.set_password(NEW_PASSWORD)
        db.session.add(user)
        db.session.commit()
        print(f"Created new owner account '{NEW_USERNAME}'.")

    print(f"You can now log in with username='{NEW_USERNAME}' and the password set in this file.")
