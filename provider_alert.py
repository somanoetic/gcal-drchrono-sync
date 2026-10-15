"""Evening alert: email tomorrow's appointments booked under another provider.

DrChrono -> GCal sync only covers DRCHRONO_DOCTOR_ID (Dr. Hadfield). Patients
mistakenly booked under another provider (e.g. Shelbiann Spence) never reach
his calendar and get missed. This step runs inside run_all.py and, once per
day at/after ALERT_HOUR local time, emails a list of tomorrow's appointments
under the watched provider(s). Silent when there are none.

Reliable 7pm timing comes from an Apps Script time trigger that fires a
repository_dispatch (see apps-script/calendar-trigger.js::eveningCheck) — the
10-min cron alone is throttled by GitHub to every 3-5h.

Set PROVIDER_ALERT_FORCE=1 to bypass the time window / once-a-day check and
always send (even an empty list) — for testing via workflow_dispatch.
"""

import base64
import datetime
import json
import os
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import config
import drchrono_client
import notify


STATE_FILE = os.path.join(os.path.dirname(__file__), ".provider_alert_state.json")
ALERT_TZ = ZoneInfo(os.getenv("PROVIDER_ALERT_TZ", "America/New_York"))
ALERT_HOUR = int(os.getenv("PROVIDER_ALERT_HOUR", "19"))
# Comma-separated last names of providers to watch
WATCH_LAST_NAMES = [
    n.strip().lower()
    for n in os.getenv("PROVIDER_ALERT_LAST_NAMES", "Spence").split(",")
    if n.strip()
]
SKIP_STATUSES = {"cancelled", "canceled", "rescheduled"}


def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _get_json(session, url):
    resp = drchrono_client._request_with_retry(session, "get", url)
    resp.raise_for_status()
    return resp.json()


def _list_all(session, url):
    results = []
    while url:
        data = _get_json(session, url)
        results.extend(data.get("results", []))
        url = data.get("next")
    return results


def _watched_doctors(session):
    doctors = _list_all(session, f"{config.DRCHRONO_API_BASE}/doctors")
    return {
        d["id"]: f"{d.get('first_name', '')} {d.get('last_name', '')}".strip()
        for d in doctors
        if (d.get("last_name") or "").strip().lower() in WATCH_LAST_NAMES
        and str(d["id"]) != str(config.DRCHRONO_DOCTOR_ID)
    }


def _patient_label(session, patient_id):
    try:
        p = _get_json(session, f"{config.DRCHRONO_API_BASE}/patients/{patient_id}")
        first = (p.get("first_name") or "?")[:1]
        last = (p.get("last_name") or "?")[:1]
        return f"{first}{last} (patient {patient_id})"
    except Exception:
        return f"patient {patient_id}"


def run():
    force = os.getenv("PROVIDER_ALERT_FORCE") == "1"
    now = datetime.datetime.now(ALERT_TZ)
    today = now.date().isoformat()
    state = _load_state()

    if not force:
        if now.hour < ALERT_HOUR:
            print(f"  Not yet {ALERT_HOUR}:00 {ALERT_TZ.key} — skipping.")
            return
        if state.get("last_checked") == today:
            print(f"  Already checked today ({today}) — skipping.")
            return

    tomorrow = (now.date() + datetime.timedelta(days=1)).isoformat()
    session = drchrono_client._get_session()

    doctors = _watched_doctors(session)
    if not doctors:
        print(f"  No DrChrono doctor found with last name in {WATCH_LAST_NAMES}.")
        return

    offices = {
        o["id"]: o.get("name", str(o["id"])) for o in drchrono_client.list_offices()
    }

    rows = []
    for doc_id, doc_name in doctors.items():
        appts = drchrono_client.fetch_appointments(tomorrow, tomorrow, doctor_id=doc_id)
        for a in appts:
            if not a.get("patient"):
                continue  # breaks / blocks
            if (a.get("status") or "").strip().lower() in SKIP_STATUSES:
                continue
            rows.append((a.get("scheduled_time", ""), doc_name, a))

    rows.sort(key=lambda r: r[0])
    print(f"  {len(rows)} appointment(s) tomorrow ({tomorrow}) under "
          f"{', '.join(doctors.values())}.")

    if rows or force:
        lines = []
        for sched, doc_name, a in rows:
            try:
                t = datetime.datetime.fromisoformat(sched).strftime("%-I:%M %p")
            except ValueError:
                t = sched
            office = offices.get(a.get("office"), a.get("office"))
            reason = a.get("reason") or ""
            lines.append(
                f"- {t}  {_patient_label(session, a['patient'])}  "
                f"[{doc_name}, {office}]" + (f"  — {reason}" if reason else "")
            )
        body = (
            f"Appointments tomorrow ({tomorrow}) booked under "
            f"{', '.join(doctors.values())}, not you. These will NOT appear on "
            "your Google Calendar.\n\n"
            + ("\n".join(lines) if lines else "(none)")
            + "\n\nIf any should be yours, change the provider in DrChrono — "
            "they'll sync to your calendar on the next run."
        )
        msg = MIMEText(body)
        msg["To"] = config.QGENDA_CALENDAR_ID
        msg["From"] = "me"
        msg["Subject"] = (
            f"DrChrono: {len(rows)} appt(s) tomorrow under "
            f"{', '.join(doctors.values())}"
        )
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        notify._build_gmail_service().users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        print(f"  Alert emailed to {config.QGENDA_CALENDAR_ID}.")

    if not force:
        state["last_checked"] = today
        _save_state(state)
