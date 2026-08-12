from app import app, check_and_send_deadline_notifications

with app.app_context():
    check_and_send_deadline_notifications()