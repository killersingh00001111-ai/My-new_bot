"""Run once, on your existing deployment: `python migrate_add_must_set_password.py`

Adds the new `must_set_password` column to the existing `users` table in
your current app.db, WITHOUT touching any existing rows/users/payment
links. All current users are set to False (they already have a working
password, nothing changes for them). Safe to run multiple times - it
checks first and does nothing if the column is already there.

Run this from the same folder as app.py (same place you'd run seed.py
or migrate_add_admin_expiry.py).
"""
import sqlite3

from config import Config

# Only works for the default sqlite setup (sqlite:///app.db). If you're
# using Postgres/MySQL via DATABASE_URL, run the equivalent ALTER TABLE
# statement with your own DB client instead.
db_path = Config.SQLALCHEMY_DATABASE_URI.replace("sqlite:///", "")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("PRAGMA table_info(users)")
existing_columns = {row[1] for row in cur.fetchall()}

if "must_set_password" not in existing_columns:
    cur.execute(
        "ALTER TABLE users ADD COLUMN must_set_password BOOLEAN NOT NULL DEFAULT 0"
    )
    print(
        "Added column: users.must_set_password "
        "(all existing users set to False - nothing changes for them)."
    )
else:
    print("Column users.must_set_password already exists - skipped.")

conn.commit()
conn.close()
print("Done. Your existing users and payment links were not changed.")
