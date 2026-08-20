"""Run once: `python seed.py` to create the initial owner account
from OWNER_USERNAME / OWNER_PASSWORD in your .env file."""
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

    existing = User.query.filter_by(username=username).first()
    if existing:
        print(f"User '{username}' already exists - nothing to do.")
    else:
        user = User(username=username, role="owner", name=name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Created owner account '{username}'.")
