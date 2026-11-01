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
3. Set `LFS_PUBLIC_BASE_URL` to `https://www.lastfanstanding.uk` (without `/login`), `LFS_SMTP_HOST` to `smtp.gmail.com`, `LFS_SMTP_PORT` to `587`, and `LFS_SMTP_SSL` to `0`.
4. Set `LFS_SMTP_USERNAME` to the full Gmail/Google Workspace email address, `LFS_SMTP_PASSWORD` to the app password, and `LFS_SMTP_FROM` to that same email address (or a sender alias already verified in Gmail). Do not invent a `no-reply@lastfanstanding.uk` sender unless it is configured and verified for that account.
5. Keep a private, stable `LFS_SECRET_KEY` at least 32 characters long. Reload the hosted app, then test with an existing registered account from the live login page.

Gmail has sending limits and is suitable for low-volume use; consider a transactional email provider as usage grows. If the host blocks outbound SMTP or Google rejects credentials, real email delivery cannot be fixed by the app alone—check hosting permissions and the Google account settings.

If an emailed reset link returns 404, check its hostname without sharing the token. The app is hosted at `www.lastfanstanding.uk`; the bare-domain redirect must preserve paths and query strings to support links such as `/reset_password?token=...`. Until that redirect is configured, use `https://www.lastfanstanding.uk` for `LFS_PUBLIC_BASE_URL`. Update the value in the private WSGI configuration before importing the app, reload the hosted app, and request a fresh reset email. For an existing, unexpired link, adding `www.` to the hostname while leaving the path and token unchanged opens the same reset flow.

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
   from app import app, prepare_database
   with app.app_context():
      prepare_database()
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

9. Push notifications - scheduled task (important):
   - The in-process APScheduler in app.py only runs when you start the site with `python app.py`
     (local use). Under WSGI on PythonAnywhere it does not run, so create a **Scheduled task**
     (Tasks tab) running **hourly** with this command:
   ```bash
   workon lfs-venv && cd /home/<your_pythonanywhere_username>/LastFanStandingApp && LFS_DB_PATH=/home/<your_pythonanywhere_username>/LastFanStandingApp/instance/last_fan_standing.db LFS_NOTIFICATIONS=1 ./run_notification_job.sh
   ```
   - The task must see the same `LFS_DB_PATH` as the WSGI file (environment variables set in the
     WSGI file are *not* visible to scheduled tasks), and `LFS_NOTIFICATIONS=1`, otherwise it exits
     silently without sending anything.
   - Output goes to `instance/notifications.log`. A lock file (`instance/notification_job.lock`)
     stops two runs overlapping. The job creates any missing tables/columns itself, so no manual
     migration step is needed - but back up the database first:
   ```bash
   cp instance/last_fan_standing.db instance/last_fan_standing.backup-$(date +%Y%m%d).db
   ```
   - Hourly timing: reminders use wide windows (48h reminder: 24-48h before the deadline; 24h
     reminder: 0-24h before; "selections revealed": up to 6h after) so an hourly task never misses
     one, but a reminder can arrive up to an hour "late" relative to the exact 48h/24h mark.
   - The VAPID key pair lives in `instance/vapid_private_key.pem` (created automatically, never
     commit it). Keep the same file on PythonAnywhere or every device will have to re-enable.

10. Who receives each notification (evaluated per competition, at the moment of sending, using the
    app's own lives calculation):
    - **48h / 24h deadline reminders** - opted-in members with more than 0 lives who have not yet
      picked for that matchweek. The 48h window is `[deadline-48h, deadline-24h)`, the 24h
      window is `[deadline-24h, deadline)`; nothing is sent once the deadline has passed. If an
      admin moves a fixture (new deadline) the reminders are raised again for the new deadline.
    - **Selections revealed** - every opted-in member of the competition, including eliminated
      players, once the matchweek deadline has passed (within a 6h grace window).
    - **New gameweek open** - opted-in members with more than 0 lives, sent when all results for a
      matchweek are recorded and a following matchweek exists.
    - Each category is controlled by its own checkbox; a player eliminated in one competition can
      still be notified for another competition they are alive in.
    - The first time a user enables push on any device all four categories are switched on; after
      that their saved choices are never changed automatically. Devices registered after an event
      was raised do not receive that old event.
    - Delivery is recorded per device (`notification_delivery`). Transient push failures are
      retried on later runs (max 3 attempts); subscriptions the push service reports as gone
      (404/410) are deleted automatically.

11. Sending a test notification to your own phone (admin, command line only - there is no web
    route for this):
   ```bash
   workon lfs-venv && cd ~/LastFanStandingApp
   export LFS_DB_PATH=/home/<your_pythonanywhere_username>/LastFanStandingApp/instance/last_fan_standing.db
   python send_test_notification.py --email you@example.com --list
   python send_test_notification.py --email you@example.com
   python send_test_notification.py --email you@example.com --type deadline_48h --competition-id 1 --matchweek 12
   ```
   The `--type` variants use the exact wording and link of the real notification for that
   competition/matchweek, go only to the named user, and are not recorded as real sends.

12. Privacy: the app stores, per user, the four notification choices and for each enabled device a
    browser push subscription (push-service URL plus two encryption keys - no device identifiers),
    plus a per-device delivery log of which competition events were sent. Users opt out on the
    Notifications page ("Disable on this device" removes the subscription; unticking all boxes
    stops all sends). There is currently no privacy policy page in the app to update.

### Optional production settings
- Keep LFS_NOTIFICATIONS=0 if you do not want push features live yet.
- Use a strong random value for LFS_SECRET_KEY.
