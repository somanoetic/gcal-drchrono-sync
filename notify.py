"""Send email notifications via Gmail API for sync conflicts and config errors."""

import base64
import datetime
import hashlib
import json
import os
from email.mime.text import MIMEText

from googleapiclient.discovery import build

import gcal_client
import config


CONFIG_ERROR_STATE_FILE = os.path.join(
    os.path.dirname(__file__), ".config_error_notify_state.json"
)
# Re-notify if a fingerprint reappears more than this many seconds after the
# last notification. 24h avoids re-spamming every 10-min cron tick but still
# alerts again the next day if the issue persists.
CONFIG_ERROR_NOTIFY_INTERVAL_SECONDS = 24 * 60 * 60


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


def _config_error_fingerprint(err):
    """Stable hash of a config error so we can dedupe across runs."""
    payload = {"office_id": err.get("office_id"), "body": err.get("body")}
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _load_config_error_state():
    if os.path.exists(CONFIG_ERROR_STATE_FILE):
        try:
            with open(CONFIG_ERROR_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_config_error_state(state):
    with open(CONFIG_ERROR_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_config_error_email(config_errors, to_email=None):
    """Send an email when DrChrono rejects blocks because a configured ID
    (office / patient / profile / doctor) is no longer valid.

    De-duped by error fingerprint with a 24h cooldown so a persistent bad
    config doesn't spam every cron tick.

    Args:
        config_errors: list of dicts with office_id and body (the parsed
            DrChrono 400 response)
        to_email: recipient (defaults to QGENDA_CALENDAR_ID / Gmail address)
    """
    if not config_errors:
        return

    if to_email is None:
        to_email = config.QGENDA_CALENDAR_ID

    state = _load_config_error_state()
    now = datetime.datetime.now().timestamp()

    # Group by fingerprint so the same problem reported 50x in one run becomes
    # one entry, and apply the cooldown
    unique = {}
    for err in config_errors:
        fp = _config_error_fingerprint(err)
        unique.setdefault(fp, {"err": err, "count": 0})
        unique[fp]["count"] += 1

    fresh = {}
    for fp, info in unique.items():
        last_sent = state.get(fp, {}).get("last_sent", 0)
        if now - last_sent >= CONFIG_ERROR_NOTIFY_INTERVAL_SECONDS:
            fresh[fp] = info

    if not fresh:
        print("Config errors detected but all are within 24h cooldown — not re-notifying.")
        return

    lines = []
    for fp, info in fresh.items():
        err = info["err"]
        count = info["count"]
        office_id = err.get("office_id", "?")
        body = err.get("body", "?")
        lines.append(
            f"- office_id={office_id}  occurrences_this_run={count}\n"
            f"  DrChrono response: {json.dumps(body) if not isinstance(body, str) else body}"
        )

    body_text = (
        f"DrChrono rejected {sum(i['count'] for i in fresh.values())} block(s) "
        "with HTTP 400 because a configured ID is no longer valid.\n\n"
        "This usually means an office, patient, profile, or doctor ID in the "
        "ENV_FILE GitHub Actions secret needs updating.\n\n"
        + "\n\n".join(lines)
        + "\n\nTo investigate, run the diagnose workflow:\n"
        "  gh workflow run diagnose.yml --ref master\n\n"
        "You'll get re-notified in 24h if the same issue persists."
    )

    msg = MIMEText(body_text)
    msg["To"] = to_email
    msg["From"] = "me"
    msg["Subject"] = f"DrChrono Sync: config error — {len(fresh)} bad ID(s)"

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    service = _build_gmail_service()
    service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()

    # Record what we just sent so we don't re-spam
    for fp in fresh:
        state[fp] = {"last_sent": now}
    _save_config_error_state(state)

    print(f"Config error notification sent to {to_email} ({len(fresh)} unique issue(s))")
