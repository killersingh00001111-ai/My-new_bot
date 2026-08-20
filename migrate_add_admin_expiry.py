"""Run once, on your existing deployment: `python migrate_add_admin_expiry.py`

Adds the two new columns (is_active, expires_at) to the existing `users`
table in your current app.db, WITHOUT touching any existing rows/users/
payment links. Safe to run multiple times - it checks first and does
nothing if the columns are already there.

Run this from the same folder as app.py (same place you'd run seed.py).
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

if "is_active" not in existing_columns:
    cur.execute("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1")
    print("Added column: users.is_active (all existing users set to active).")
else:
    print("Column users.is_active already exists - skipped.")

if "expires_at" not in existing_columns:
    cur.execute("ALTER TABLE users ADD COLUMN expires_at DATETIME")
    print("Added column: users.expires_at (all existing users set to NULL = never expires).")
else:
    print("Column users.expires_at already exists - skipped.")

conn.commit()
conn.close()
print("Done. Your existing users and payment links were not changed.")
