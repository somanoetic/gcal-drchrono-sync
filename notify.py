"""Send email notifications via Gmail API for sync conflicts."""

import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build

import gcal_client
import config


def _build_gmail_service():
    creds = gcal_client._get_credentials()
    return build("gmail", "v1", credentials=creds)


def send_conflict_email(conflicts, to_email=None):
    """Send an email listing DrChrono blocks that failed due to conflicts.

    Args:
        conflicts: list of dicts with summary, scheduled_time, duration, calendar_id
        to_email: recipient (defaults to QGENDA_CALENDAR_ID / Gmail address)
    """
    if not conflicts:
        return

    if to_email is None:
        to_email = config.QGENDA_CALENDAR_ID  # hadfield.neil@gmail.com

    lines = []
    for c in conflicts:
        lines.append(f"- {c['summary']} on {c['scheduled_time']} ({c['duration']}min)")

    body_text = (
        f"{len(conflicts)} calendar block(s) could NOT be created in DrChrono "
        "because a patient appointment or other event already exists in that time slot.\n\n"
        "You may need to reschedule these patients:\n\n"
        + "\n".join(lines)
        + "\n\nCheck DrChrono to see what's booked in those slots."
    )

    msg = MIMEText(body_text)
    msg["To"] = to_email
    msg["From"] = "me"
    msg["Subject"] = f"DrChrono Sync: {len(conflicts)} conflict(s) need attention"

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    service = _build_gmail_service()
    service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()

    print(f"Conflict notification sent to {to_email}")
