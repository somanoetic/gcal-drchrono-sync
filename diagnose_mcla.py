"""One-shot: investigate MCLA 9am appointment for profile-name mismatch.

Reports:
- All DrChrono appointments today, with patient name and profile name
- Matching GCal events on DRCHRONO_PATIENT_CALENDAR_ID for today
- The stored event_map entry for the MCLA appointment (if any)
- Whether _enrich_from_api would match correctly
"""

import datetime
import json
import os

import config
import drchrono_client
import gcal_client


def _section(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main():
    today = datetime.date.today().isoformat()

    _section(f"1. DrChrono appointments on {today}")
    appts = drchrono_client.fetch_appointments(today, today)
    profile_map = drchrono_client.fetch_appointment_profiles()

    print(f"Found {len(appts)} appointment(s).")
    mcla_appts = []
    for a in appts:
        sched = a.get("scheduled_time", "")
        profile_id = a.get("profile")
        profile_info = profile_map.get(profile_id, {}) if profile_id else {}
        profile_name = profile_info.get("name", "?") if isinstance(profile_info, dict) else profile_info
        patient_id = a.get("patient")
        is_break = a.get("appt_is_break", False)
        is_telehealth = a.get("is_telehealth", False)
        notes = (a.get("notes") or "")[:40]
        reason = (a.get("reason") or "")[:40]
        print(f"  {sched}  appt_id={a.get('id')}  patient={patient_id}  "
              f"profile_id={profile_id}  profile_name={profile_name!r}  "
              f"break={is_break}  telehealth={is_telehealth}")
        if reason:
            print(f"      reason: {reason!r}")
        if notes:
            print(f"      notes:  {notes!r}")

        # Heuristic for MCLA-9am
        if "09:00" in sched or sched.endswith("T09:00:00"):
            mcla_appts.append(a)

    _section(f"2. Looking up the patient for any 9am appointments")
    for a in mcla_appts:
        pid = a.get("patient")
        if not pid:
            continue
        session = drchrono_client._get_session()
        resp = session.get(f"{config.DRCHRONO_API_BASE}/patients/{pid}")
        if resp.status_code == 200:
            p = resp.json()
            print(f"  patient_id={pid}  first={p.get('first_name')!r}  "
                  f"last={p.get('last_name')!r}")
        else:
            print(f"  patient_id={pid}  lookup failed: HTTP {resp.status_code}")

    _section("3. GCal events on DRCHRONO - Patient Appointments today")
    patient_cal = config.DRCHRONO_PATIENT_CALENDAR_ID
    if not patient_cal:
        patient_cal = gcal_client.find_or_create_calendar(config.DRCHRONO_PATIENT_CALENDAR_NAME)
    print(f"Calendar: {patient_cal}")

    service = gcal_client._build_service()
    start_iso = f"{today}T00:00:00-04:00"
    end_iso = f"{today}T23:59:59-04:00"
    resp = service.events().list(
        calendarId=patient_cal,
        timeMin=start_iso,
        timeMax=end_iso,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = resp.get("items", [])
    print(f"Found {len(events)} GCal event(s).")
    for e in events:
        start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "?"))
        summary = e.get("summary", "?")
        eid = e.get("id")
        props = e.get("extendedProperties", {}).get("private", {})
        created_by = props.get("createdBy", "?")
        stable_key = props.get("stableKey", "?")
        print(f"  {start}  summary={summary!r}  gcal_id={eid}")
        print(f"      createdBy={created_by!r}  stableKey={stable_key!r}")

    _section("4. State file: event_map entries for today")
    state_file = config.DRCHRONO_SYNC_STATE_FILE
    if not os.path.exists(state_file):
        print(f"State file missing: {state_file}")
        return
    with open(state_file) as f:
        state = json.load(f)
    event_map = state.get("event_map", {})
    today_entries = [(uid, info) for uid, info in event_map.items()
                     if today in info.get("dtstart", "")]
    print(f"Found {len(today_entries)} event_map entry(ies) for today.")
    for uid, info in today_entries:
        print(f"  uid={uid}")
        print(f"      summary    = {info.get('summary')!r}")
        print(f"      dtstart    = {info.get('dtstart')}")
        print(f"      dtend      = {info.get('dtend')}")
        print(f"      gcal_id    = {info.get('gcal_event_id')}")
        print(f"      cal_id     = {info.get('calendar_id')}")


if __name__ == "__main__":
    main()
