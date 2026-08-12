# Last Fan Standing

A Flask-based MVP for a Last Man Standing style football prediction game.

## What is included
- User registration and login
- Password reset flow
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
