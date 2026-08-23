"""Run once, on your existing deployment: `python migrate_add_duration_and_first_login.py`

Adds the two new columns (duration_seconds, first_login_at) to the
existing `users` table in your current app.db, WITHOUT touching any
existing rows/users/payment links. Safe to run multiple times - it
checks first and does nothing if the columns are already there.

Why this is needed: admin accounts used to start their expiry countdown
the moment the owner created them (expires_at was set right away). Now
the countdown only starts once that admin actually logs in for the
first time - duration_seconds stores how long their access should last,
and first_login_at records when they first logged in, so expires_at can
be computed from that moment instead of from account-creation time.

Run this from the same folder as app.py (same place you'd run seed.py
or migrate_add_admin_expiry.py).
"""
import sqlite3

from config import Config

# Only works for the default sqlite setup (sqlite:///app.db). If you're
# using Postgres/MySQL via DATABASE_URL, run the equivalent ALTER TABLE
# statements with your own DB client instead.
db_path = Config.SQLALCHEMY_DATABASE_URI.replace("sqlite:///", "")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("PRAGMA table_info(users)")
existing_columns = {row[1] for row in cur.fetchall()}

if "duration_seconds" not in existing_columns:
    cur.execute("ALTER TABLE users ADD COLUMN duration_seconds INTEGER")
    print("Added column: users.duration_seconds (all existing users set to NULL = no time-boxed duration).")
else:
    print("Column users.duration_seconds already exists - skipped.")

if "first_login_at" not in existing_columns:
    cur.execute("ALTER TABLE users ADD COLUMN first_login_at DATETIME")
    print("Added column: users.first_login_at (all existing users set to NULL).")
else:
    print("Column users.first_login_at already exists - skipped.")

conn.commit()
conn.close()
print("Done. Your existing users and payment links were not changed.")
print()
print(
    "Note: any admin accounts created BEFORE this migration that already "
    "have an expires_at set will keep counting down from that existing "
    "value (their duration_seconds/first_login_at will just be NULL) - "
    "only admins created from now on get the new 'starts on first login' "
    "behaviour."
)
