# Last Fan Standing

A Flask-based MVP for a Last Man Standing style football prediction game.

## What is included
- User registration and login
- Logged-in password changes (current password required)
- Secure email-link password recovery (SMTP configuration required)
- Competition creation and joining using a competition code
- Matchweek-based pick submission with unsaved-changes warning
- Home page showing the current weekend pick
- Fixtures, league table, and view-selections screens
- Basic elimination logic using three lives per competition

## Run locally
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the app:
   ```bash
   python app.py
   ```
3. Open http://127.0.0.1:5000

## Notes
- The app uses SQLite for development and is ready to be adapted for PythonAnywhere by switching the database path and configuring the WSGI entry point.
- Logged-in users can choose **Change password** from the menu, enter their current password, and choose a new 12–128 character password. Successful changes sign out the current browser so the user can log in with the new password.
- **Forgot password?** sends a single-use reset link when SMTP is configured. Existing passwords remain unchanged until a valid link is submitted with a new password.
- Password forms have session-bound CSRF protection. Password changes and resets invalidate all existing sessions and remembered logins.
- The default administrator's password is set only when that account is created, not overwritten on subsequent startups.

## Configure email password recovery

Recovery stays disabled until all required configuration is present. No real emails are sent by the automated tests.

| Environment variable | Value |
| --- | --- |
| `LFS_SECRET_KEY` | A private random secret at least 32 characters long; keep it stable across reloads. |
| `LFS_PUBLIC_BASE_URL` | Your trusted public HTTPS website URL, e.g. `https://yourname.pythonanywhere.com` (not the local preview address). |
| `LFS_SMTP_HOST` | Your mail provider's SMTP hostname. |
| `LFS_SMTP_PORT` | Usually `587` for STARTTLS or `465` for implicit TLS. Default: `587`. |
| `LFS_SMTP_SSL` | `0` for STARTTLS (default), `1` for implicit TLS. Unencrypted SMTP is not supported. |
| `LFS_SMTP_USERNAME` | The SMTP username issued by your provider. |
| `LFS_SMTP_PASSWORD` | The provider's SMTP credential/app password, not committed to Git. |
| `LFS_SMTP_FROM` | A sender address verified by your provider, e.g. `Last Fan Standing <no-reply@yourdomain.com>`. |

### PythonAnywhere setup
1. Choose a transactional mail provider that supports authenticated SMTP. Verify your sender/domain in its dashboard and configure the SPF/DKIM records it supplies.
2. In **Web → your app → WSGI configuration file**, set the environment variables above alongside the existing `LFS_DB_PATH` setting, **before** importing the Flask app. Enter SMTP secrets directly in your private hosting configuration; never paste them into chat or commit them.
3. Back up your database, deploy the updated code, then click **Reload** on the Web tab. New reset-token and rate-limit tables are created automatically; existing user rows need no schema change.
4. This release signs out pre-existing sessions once, because login cookies now include a password-bound identifier. Users simply log in again.
5. On the live login page, click **Forgot password?**, use a real registered test account, check its inbox/spam folder, follow the link, and verify that the old password no longer works.
6. If delivery fails, check the provider's delivery dashboard and the PythonAnywhere error log for `Password recovery delivery failed`. Confirm your hosting plan permits outbound SMTP connections to the chosen host/port. Upgrade/change provider if needed; do not disable TLS verification.

### Gmail setup for lastfanstanding.uk
1. Sign in to the Google account you want to send from. Open **Google Account → Security → 2-Step Verification** and enable it.
2. Open Google's **App passwords** page at https://myaccount.google.com/apppasswords, create an app password named **Last Fan Standing**, and enter it directly in your private hosting configuration. Do not use your normal Google password. App passwords may be unavailable on restricted/managed accounts.
3. Set `LFS_PUBLIC_BASE_URL` to `https://lastfanstanding.uk` (without `/login`), `LFS_SMTP_HOST` to `smtp.gmail.com`, `LFS_SMTP_PORT` to `587`, and `LFS_SMTP_SSL` to `0`.
4. Set `LFS_SMTP_USERNAME` to the full Gmail/Google Workspace email address, `LFS_SMTP_PASSWORD` to the app password, and `LFS_SMTP_FROM` to that same email address (or a sender alias already verified in Gmail). Do not invent a `no-reply@lastfanstanding.uk` sender unless it is configured and verified for that account.
5. Keep a private, stable `LFS_SECRET_KEY` at least 32 characters long. Reload the hosted app, then test with an existing registered account from the live login page.

Gmail has sending limits and is suitable for low-volume use; consider a transactional email provider as usage grows. If the host blocks outbound SMTP or Google rejects credentials, real email delivery cannot be fixed by the app alone—check hosting permissions and the Google account settings.

Links expire after 30 minutes; only hashed tokens are stored. Opening a link does not consume it (email scanners are safe). A successful reset invalidates all other links issued before that password change. Requests are limited to 10 per IP and 3 per email per 15-minute fixed window; password submissions to 20 per IP per window. Limits are stored in SQLite and survive restarts. The app uses `request.remote_addr`, not untrusted forwarded headers; configure a trusted proxy explicitly if your host supplies a proxy address, otherwise clients may share a limit.

Unknown addresses, ambiguous legacy case-duplicate accounts, suppressed requests, and SMTP failures all receive the same generic confirmation. SMTP delivery is synchronous with a 15-second timeout; very high-volume deployments should use a background mail queue to reduce latency and timing differences.

Treat reset links as credentials: redact `token` query parameters in reverse-proxy/access logs and do not send them to analytics. The app immediately redirects to a clean form URL and sets `no-store` and `no-referrer` headers. It never logs reset tokens, SMTP credentials, or provider exception text. Pending/expired token and rate-limit records are pruned during recovery activity.

## Deploy on PythonAnywhere
1. Create an account on PythonAnywhere and open a Bash console.

2. Clone or upload this project into your home directory.

3. Create and activate a virtual environment with Python 3.13:
   ```bash
   mkvirtualenv --python=/usr/bin/python3.13 lfs-venv
   workon lfs-venv
   cd ~/LastFanStandingApp
   pip install -r requirements.txt
   ```

4. Create the SQLite DB and tables:
   ```bash
   export LFS_DB_PATH=/home/<your_pythonanywhere_username>/LastFanStandingApp/instance/last_fan_standing.db
   python - <<'PY'
   from app import app, db
   with app.app_context():
      db.create_all()
   print("Database initialized")
   PY
   ```

5. In the PythonAnywhere Web tab:
   - Create a new web app (Manual configuration, Python 3.13).
   - Set Virtualenv to /home/<your_pythonanywhere_username>/.virtualenvs/lfs-venv
   - Set Source code to /home/<your_pythonanywhere_username>/LastFanStandingApp

6. Edit your WSGI file and use:
   ```python
   import os
   import sys

   project_home = '/home/<your_pythonanywhere_username>/LastFanStandingApp'
   if project_home not in sys.path:
      sys.path.insert(0, project_home)

   os.environ['LFS_SECRET_KEY'] = '<set-a-long-random-secret>'
   os.environ['LFS_DB_PATH'] = '/home/<your_pythonanywhere_username>/LastFanStandingApp/instance/last_fan_standing.db'
   os.environ['LFS_NOTIFICATIONS'] = '1'

   from app import app as application
   ```

7. Set static file mappings in the Web tab:
   - URL: /static/
   - Directory: /home/<your_pythonanywhere_username>/LastFanStandingApp/static/

8. Reload the web app from the Web tab.

9. Notifications scheduling (important):
   - APScheduler in app.py only runs when starting with python app.py.
   - Under WSGI on PythonAnywhere, create a Scheduled task (every 15 minutes) that runs the helper script and writes to a proper log file:
   ```bash
   workon lfs-venv && cd /home/<your_pythonanywhere_username>/LastFanStandingApp && ./run_notification_job.sh
   ```
   - The helper script writes to instance/notifications.log so the scheduler never appends output to the Python file itself.

### Optional production settings
- Keep LFS_NOTIFICATIONS=0 if you do not want push features live yet.
- Use a strong random value for LFS_SECRET_KEY.
