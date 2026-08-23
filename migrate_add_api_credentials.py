"""Run once, on your existing deployment: `python migrate_add_api_credentials.py`

Adds 3 new columns (source_platform, callback_url, api_credential_id) to
the EXISTING `payment_links` table, for links created through the new
external API (an admin's Android app / website / Telegram bot) instead
of directly inside the panel. Every existing row is left as NULL for all
three - nothing about them changes.

The new `api_credentials` table itself does NOT need this script - it's
brand new, so Flask-SQLAlchemy's db.create_all() (which already runs
automatically every time the app starts, in app.py) creates it on its
own the next time the app starts. Nothing to do for that part.

Safe to run more than once - it checks first and does nothing if the
columns are already there. Run this from the same folder as app.py.
"""
import sqlite3

from config import Config

# Only works for the default sqlite setup (sqlite:///app.db). If you're
# using Postgres/MySQL via DATABASE_URL, run the equivalent ALTER TABLE
# statements with your own DB client instead.
db_path = Config.SQLALCHEMY_DATABASE_URI.replace("sqlite:///", "")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("PRAGMA table_info(payment_links)")
existing_columns = {row[1] for row in cur.fetchall()}

added_any = False

if "source_platform" not in existing_columns:
    cur.execute("ALTER TABLE payment_links ADD COLUMN source_platform VARCHAR(20)")
    print("Added column: payment_links.source_platform")
    added_any = True
else:
    print("Column payment_links.source_platform already exists - skipped.")

if "callback_url" not in existing_columns:
    cur.execute("ALTER TABLE payment_links ADD COLUMN callback_url VARCHAR(500)")
    print("Added column: payment_links.callback_url")
    added_any = True
else:
    print("Column payment_links.callback_url already exists - skipped.")

if "api_credential_id" not in existing_columns:
    cur.execute("ALTER TABLE payment_links ADD COLUMN api_credential_id INTEGER")
    print("Added column: payment_links.api_credential_id")
    added_any = True
else:
    print("Column payment_links.api_credential_id already exists - skipped.")

conn.commit()
conn.close()

if added_any:
    print("Done. Existing payment links were not changed (new columns are NULL for them).")
else:
    print("Nothing to do - all columns already existed.")

print()
print(
    "Note: the new api_credentials table is created automatically the "
    "next time the app starts (via db.create_all() in app.py) - no "
    "extra step needed for that part."
)
