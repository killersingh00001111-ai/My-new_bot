"""Run once, any time your login stops working: `python reset_owner_password.py`

Why you need this file:
seed.py only CREATES the owner account the very first time. If you ever
edited OWNER_USERNAME / OWNER_PASSWORD in .env AFTER the account already
existed in the database, seed.py silently does nothing ("already exists -
nothing to do") - so the database keeps the OLD password hash forever,
which is almost certainly why typing your current .env password now says
"Invalid username or password."

This script is different: it force-overwrites the stored password hash
for OWNER_USERNAME with whatever OWNER_PASSWORD currently is in .env - so
after running it, you can log in with exactly what's in .env right now,
with no other steps. If the account doesn't exist yet, it creates it
instead (same as seed.py would).
"""
import os

from app import app  # noqa: also loads .env via config.py
from extensions import db
from models import User

with app.app_context():
    username = os.environ.get("OWNER_USERNAME")
    password = os.environ.get("OWNER_PASSWORD")
    name = os.environ.get("OWNER_NAME", "Owner")

    if not username or not password:
        raise SystemExit("Set OWNER_USERNAME and OWNER_PASSWORD in your .env first.")

    user = User.query.filter_by(username=username).first()

    if user:
        user.set_password(password)
        db.session.commit()
        print(f"Password for existing user '{username}' has been reset to match .env.")
    else:
        user = User(username=username, role="owner", name=name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Created owner account '{username}'.")

    print("You can now log in with the username/password currently set in .env.")
