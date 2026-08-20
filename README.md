# Custom Payment Admin (Einpays integration)

Flask app: admin login, "Create Payment Link" (calls Einpays to get a payment
link + generates a QR code with an expiry timer), a webhook endpoint that
verifies and receives Einpays' signed callbacks, an owner-only admin panel
(add admin/owner users, change site background), and a profile page
(name, bio, avatar).

## 1. Install

```bash
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

## 2. Where to put your keys / IDs (exact lines to edit)

- **`keys/private.pem`** — replace line 2 with your real RSA private key body
  (the whole `-----BEGIN/END PRIVATE KEY-----` block). Keep this file secret,
  never commit it or share it.
- **`keys/public.pem`** — replace line 2 with the matching public key (the
  same one you already gave Einpays).
- **`keys/einpays_response_public.pem`** and **`keys/einpays_callback_public.pem`**
  — already filled in from the docs you shared. Only replace these if Einpays
  ever rotates their keys.
- **`.env`** — set:
  - `EINPAYS_CLIENT_ID` (line 20) — use whichever of your client_ids
    (528/529/530/531 etc.) this instance should use.
  - `RESET_OTP_EMAIL` (line 10) — already set to the inbox you specified.
  - `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` (lines 17-19) — a real
    mailbox to actually send the OTP email. Without these, the OTP is only
    logged to the server console (fine for local testing, not for production).
  - `OWNER_USERNAME` / `OWNER_PASSWORD` (lines 7-8) — already set to what you
    gave; change them before going live, and consider a stronger password.

**Create-deposit endpoint — confirmed.** Per the Einpays "Common Deposits
API" PDF, the create-deposit request is sent to `/api/v5/methods/get` (that
single endpoint both lists methods and creates the deposit / returns the
payment link). `EINPAYS_CREATE_DEPOSIT_ENDPOINT` in `.env` is now set to
that value. JWT signing and response parsing match the documented format.

## 3. Create the database + first owner account

```bash
python seed.py
```

## 4. Run

```bash
python app.py
```

Visit `http://localhost:5000`, log in with the owner account, then use
**Create Payment Link** to generate a link + QR code, or the **Admin Panel**
to add more admins/owners and change the background image.

## 5. Point Einpays' callback at you

Give Einpays this URL as your callback/webhook endpoint:

```
https://YOUR-DOMAIN/webhooks/einpays
```

The app verifies every callback's signature against
`keys/einpays_callback_public.pem` before updating any transaction status —
unsigned or badly-signed requests are rejected with HTTP 400.

## Deploying on PythonAnywhere

The two errors from your log are now fixed in this version:

- **`ModuleNotFoundError: No module named 'extensions'`** — happened because
  the folder containing `extensions.py` (and the other project files)
  wasn't guaranteed to be on Python's import path when the WSGI server
  started. `app.py` now adds its own folder to `sys.path` at the top, so
  this works regardless of PythonAnywhere's working directory.
- **`ImportError: cannot import name 'app' from 'app'`** — PythonAnywhere's
  auto-generated WSGI file does `from app import app as application`, which
  needs a variable literally named `app` at module level. The old code only
  had `create_app()`. `app.py` now also has `app = create_app()` at the
  bottom of the file, so that import works.

Steps:

1. Upload **every** file/folder from this zip (not just `app.py`) into the
   same directory on PythonAnywhere, e.g. `/home/Rimonp55/` — keep
   `templates/`, `static/`, and `keys/` as subfolders right next to `app.py`.
2. In a PythonAnywhere Bash console, `cd` into that folder and run:
   ```bash
   pip install --user -r requirements.txt
   cp .env.example .env      # then edit .env with your real values
   python seed.py
   ```
3. On the **Web** tab, make sure the WSGI file still has
   `from app import app as application` (that's the default PythonAnywhere
   generates and it now matches this code).
4. Click **Reload** on the Web tab.

If you still see an import error after this, check the error log for the
exact missing module name — it almost always means a file from this zip
wasn't uploaded, or `pip install` didn't run inside the same virtualenv
PythonAnywhere is using for the web app.

## Notes / recommendations

- Passwords are hashed (never stored in plain text), even though you gave a
  literal password in your spec — the login form still accepts exactly what
  you typed.
- Run this behind HTTPS in production (e.g. behind Nginx/Caddy or a platform
  that terminates TLS) since it handles payment data and login credentials.
- `keys/private.pem` and `.env` should never be committed to git — add them
  to `.gitignore` before pushing anywhere.
