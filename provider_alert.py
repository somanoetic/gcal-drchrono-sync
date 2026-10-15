"""Evening email: tomorrow's full DrChrono schedule, across all providers.

DrChrono -> GCal sync only covers DRCHRONO_DOCTOR_ID (Dr. Hadfield). Patients
mistakenly booked under another provider (e.g. Shelbiann Spence) never reach
his calendar and get missed. This step runs inside run_all.py and, once per
day at/after ALERT_HOUR local time, emails every appointment tomorrow for
every provider in the practice, flagging the ones not under him.

Reliable 7pm timing comes from an Apps Script time trigger that fires a
repository_dispatch (see apps-script/calendar-trigger.js::eveningCheck) — the
10-min cron alone is throttled by GitHub to every 3-5h.

Set PROVIDER_ALERT_FORCE=1 to bypass the time window / once-a-day check —
for testing via workflow_dispatch.
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


def _all_doctors(session):
    doctors = _list_all(session, f"{config.DRCHRONO_API_BASE}/doctors")
    return {
        d["id"]: f"{d.get('first_name', '')} {d.get('last_name', '')}".strip()
        for d in doctors
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
            print(f"  Already sent today ({today}) — skipping.")
            return

    tomorrow = (now.date() + datetime.timedelta(days=1)).isoformat()
    session = drchrono_client._get_session()

    doctors = _all_doctors(session)
    my_id = str(config.DRCHRONO_DOCTOR_ID)
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
            rows.append((a.get("scheduled_time", ""), str(doc_id) == my_id, doc_name, a))

    rows.sort(key=lambda r: r[0])
    others = [r for r in rows if not r[1]]
    print(f"  {len(rows)} appointment(s) tomorrow ({tomorrow}), "
          f"{len(others)} under other providers.")

    lines = []
    for sched, mine, doc_name, a in rows:
        try:
            t = datetime.datetime.fromisoformat(sched).strftime("%-I:%M %p")
        except ValueError:
            t = sched
        office = offices.get(a.get("office"), a.get("office"))
        reason = a.get("reason") or ""
        flag = "" if mine else "  ** NOT ON YOUR CALENDAR **"
        lines.append(
            f"- {t}  {_patient_label(session, a['patient'])}  "
            f"[{doc_name}, {office}]" + (f"  — {reason}" if reason else "") + flag
        )

    day = datetime.date.fromisoformat(tomorrow).strftime("%A %b %-d")
    body = (
        f"All DrChrono appointments for {day}, every provider "
        f"({len(rows)} total).\n\n"
        + ("\n".join(lines) if lines else "(no appointments)")
    )
    if others:
        body += (
            f"\n\n{len(others)} appointment(s) flagged ** are booked under "
            "another provider and will NOT appear on your Google Calendar. "
            "If any should be yours, change the provider in DrChrono."
        )
    subject = f"DrChrono schedule {day}: {len(rows)} appt(s)"
    if others:
        subject += f" — {len(others)} under another provider"

    msg = MIMEText(body)
    msg["To"] = config.QGENDA_CALENDAR_ID
    msg["From"] = "me"
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    notify._build_gmail_service().users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    print(f"  Schedule emailed to {config.QGENDA_CALENDAR_ID}.")

    if not force:
        state["last_checked"] = today
        _save_state(state)
