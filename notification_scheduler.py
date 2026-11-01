"""Legacy entry point - kept so any existing scheduled task keeps working.
Delegates to run_notification_job so there is a single, locked job implementation."""
from run_notification_job import main

if __name__ == "__main__":
    raise SystemExit(main())
