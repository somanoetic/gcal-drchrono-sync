"""Filtered sync: DrChrono ICS feed -> Google Calendar.

Replaces the built-in DrChrono ICS subscription so we can skip echo events
(blocks we created via sync.py that DrChrono syncs back).

- Patient appointments  -> DRCHRONO_PATIENT_CALENDAR_ID
- Non-patient events    -> DRCHRONO_OTHER_CALENDAR_ID
- Block echoes (UNTI07E4E294) -> skipped

Usage:
    python drchrono_to_gcal.py          # normal sync
    python drchrono_to_gcal.py --full   # delete all managed events and re-sync
"""

import json
import os
import sys
import datetime

import requests
from icalendar import Calendar
from googleapiclient.errors import HttpError

import config
import drchrono_client
import gcal_client


def load_state():
    if os.path.exists(config.DRCHRONO_SYNC_STATE_FILE):
        with open(config.DRCHRONO_SYNC_STATE_FILE) as f:
            return json.load(f)
    # event_map: ics_uid -> {gcal_event_id, calendar_id, summary, dtstart, dtend}
    return {"event_map": {}, "last_fetch": None}


def save_state(state):
    with open(config.DRCHRONO_SYNC_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _fetch_ics():
    """Fetch and parse the DrChrono ICS feed."""
    resp = requests.get(config.DRCHRONO_ICS_URL, timeout=30)
    resp.raise_for_status()
    return Calendar.from_ical(resp.text)


def _resolve_calendar_ids():
    """Resolve calendar IDs, creating calendars if needed.

    Uses explicit IDs from config if set, otherwise finds or creates
    calendars by name under the authenticated Google account.
    """
    patient_cal = config.DRCHRONO_PATIENT_CALENDAR_ID
    if not patient_cal:
        patient_cal = gcal_client.find_or_create_calendar(config.DRCHRONO_PATIENT_CALENDAR_NAME)

    other_cal = config.DRCHRONO_OTHER_CALENDAR_ID
    if not other_cal:
        other_cal = gcal_client.find_or_create_calendar(config.DRCHRONO_OTHER_CALENDAR_NAME)

    return patient_cal, other_cal


def _is_block_echo(summary):
    """Return True if this event is an echo of a block we created."""
    return config.DRCHRONO_BLOCK_PATIENT_NAME in (summary or "")


def _is_patient_appointment(summary):
    """Return True if this looks like a patient appointment."""
    return (summary or "").startswith("Appointment with")


def _target_calendar(summary, patient_cal_id, other_cal_id):
    """Decide which Google Calendar to write this event to."""
    if _is_patient_appointment(summary):
        return patient_cal_id
    return other_cal_id


def _stable_key(summary, dtstart, dtend):
    """Build a stable key from event content since DrChrono ICS UIDs change each fetch."""
    start_iso = _dt_to_iso(dtstart)
    end_iso = _dt_to_iso(dtend)
    return f"{summary}|{start_iso}|{end_iso}"


def _dt_to_iso(dt_val):
    """Convert an icalendar date/datetime to ISO string."""
    if isinstance(dt_val, datetime.datetime):
        return dt_val.isoformat()
    if isinstance(dt_val, datetime.date):
        return dt_val.isoformat()
    return str(dt_val)


def _clean_summary(summary, description=""):
    """Clean up event title for Google Calendar display.

    For non-patient events, use the description (DrChrono "Reason" field)
    as the title if available.
    """
    if not _is_patient_appointment(summary) and description.strip():
        return description.strip()
    # DrChrono prefixes non-patient events with "Break " — strip it
    if summary.startswith("Break "):
        return summary[6:]
    return summary


def _build_gcal_body(summary, dtstart, dtend, description=""):
    """Build a Google Calendar event body from ICS data."""
    body = {
        "summary": _clean_summary(summary, description),
        "extendedProperties": {
            "private": {"createdBy": config.DRCHRONO_SYNC_TAG}
        },
    }

    # Handle all-day vs timed events
    if isinstance(dtstart, datetime.datetime):
        body["start"] = {"dateTime": dtstart.isoformat()}
        body["end"] = {"dateTime": dtend.isoformat()}
    else:
        body["start"] = {"date": dtstart.isoformat()}
        body["end"] = {"date": dtend.isoformat()}

    return body


def _safe_delete(calendar_id, event_id):
    """Delete a Google Calendar event, swallowing 404/410."""
    try:
        gcal_client.delete_event(calendar_id, event_id)
        return True
    except HttpError as e:
        if e.resp.status in (404, 410):
            return False
        raise


def _enrich_with_reasons(ics_events):
    """Look up the DrChrono 'reason' field via the API for non-patient events.

    Mutates ics_events in-place, setting evt["description"] to the reason
    when one is found.
    """
    # Collect the date range we need to query
    non_patient = {uid: evt for uid, evt in ics_events.items()
                   if not _is_patient_appointment(evt["summary"])}
    if not non_patient:
        return

    dates = []
    for evt in non_patient.values():
        dt = evt["dtstart"]
        dates.append(dt.date() if isinstance(dt, datetime.datetime) else dt)
    date_start = min(dates).isoformat()
    date_end = max(dates).isoformat()

    print(f"  Fetching appointment reasons from API ({date_start} to {date_end})...")
    try:
        api_appts = drchrono_client.fetch_appointments(date_start, date_end)
    except Exception as e:
        print(f"  WARNING: Could not fetch reasons from API: {e}")
        return

    # Build lookup: scheduled_time -> reason (for non-patient / break appointments)
    reason_lookup = {}
    for appt in api_appts:
        sched = appt.get("scheduled_time", "")
        reason = (appt.get("reason") or "").strip()
        if reason and sched:
            reason_lookup[sched] = reason

    # Match ICS events to API appointments by start time
    matched = 0
    for uid, evt in non_patient.items():
        dt = evt["dtstart"]
        if not isinstance(dt, datetime.datetime):
            continue
        # Strip timezone for matching — DrChrono API returns naive local times
        naive = dt.replace(tzinfo=None)
        api_time = naive.strftime("%Y-%m-%dT%H:%M:%S")
        reason = reason_lookup.get(api_time, "")
        if reason:
            evt["description"] = reason
            matched += 1

    print(f"  Matched {matched} reason(s) from API for {len(non_patient)} non-patient event(s).")


def run():
    force_full = "--full" in sys.argv

    if not config.DRCHRONO_ICS_URL:
        print("DRCHRONO_ICS_URL not set, skipping DrChrono -> Google sync.")
        return

    state = load_state()
    event_map = state.get("event_map", {})

    print("DrChrono -> Google Calendar filtered sync")

    # Resolve target calendar IDs (creates calendars on first run)
    patient_cal_id, other_cal_id = _resolve_calendar_ids()
    print(f"  Patient calendar: {patient_cal_id}")
    print(f"  Office calendar:  {other_cal_id}")

    # On --full, delete all managed events first
    if force_full and event_map:
        print(f"  Full sync: removing {len(event_map)} managed event(s)...")
        for uid, info in list(event_map.items()):
            _safe_delete(info["calendar_id"], info["gcal_event_id"])
            del event_map[uid]

    # Fetch ICS feed
    print("  Fetching DrChrono ICS feed...")
    cal = _fetch_ics()

    # Parse all VEVENT components
    ics_events = {}
    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", ""))
        if not uid:
            continue

        summary = str(component.get("SUMMARY", ""))
        description = str(component.get("DESCRIPTION", ""))
        dtstart = component.get("DTSTART")
        dtend = component.get("DTEND")

        if not dtstart or not dtend:
            continue

        dtstart = dtstart.dt
        dtend = dtend.dt

        # Skip block echoes
        if _is_block_echo(summary):
            continue

        target_cal = _target_calendar(summary, patient_cal_id, other_cal_id)
        if not target_cal:
            continue

        key = _stable_key(summary, dtstart, dtend)
        ics_events[key] = {
            "summary": summary,
            "description": description,
            "dtstart": dtstart,
            "dtend": dtend,
            "calendar_id": target_cal,
        }

    print(f"  Found {len(ics_events)} event(s) (after filtering block echoes).")

    # Fetch appointment reasons from the DrChrono API for non-patient events.
    # The ICS feed doesn't include the "Reason" field, so we look it up via API.
    _enrich_with_reasons(ics_events)

    created = 0
    updated = 0
    deleted = 0

    # Create or update events
    for uid, evt in ics_events.items():
        summary = evt["summary"]
        description = evt.get("description", "")
        dtstart = evt["dtstart"]
        dtend = evt["dtend"]
        target_cal = evt["calendar_id"]
        gcal_body = _build_gcal_body(summary, dtstart, dtend, description)

        start_iso = _dt_to_iso(dtstart)
        end_iso = _dt_to_iso(dtend)

        if uid in event_map:
            existing = event_map[uid]
            # Check if anything changed
            if (existing["summary"] == summary
                    and existing["dtstart"] == start_iso
                    and existing["dtend"] == end_iso
                    and existing["calendar_id"] == target_cal):
                continue

            # If calendar changed, delete from old and create in new
            if existing["calendar_id"] != target_cal:
                _safe_delete(existing["calendar_id"], existing["gcal_event_id"])
                try:
                    new_event = gcal_client.create_event(target_cal, gcal_body)
                    event_map[uid] = {
                        "gcal_event_id": new_event["id"],
                        "calendar_id": target_cal,
                        "summary": summary,
                        "dtstart": start_iso,
                        "dtend": end_iso,
                    }
                    updated += 1
                    print(f"  Moved: {summary} -> {target_cal}")
                except Exception as e:
                    print(f"  WARNING: Failed to move {summary}: {e}")
                continue

            # Same calendar, update in place
            try:
                gcal_client.update_event(target_cal, existing["gcal_event_id"], gcal_body)
                event_map[uid] = {
                    "gcal_event_id": existing["gcal_event_id"],
                    "calendar_id": target_cal,
                    "summary": summary,
                    "dtstart": start_iso,
                    "dtend": end_iso,
                }
                updated += 1
                print(f"  Updated: {summary} ({start_iso})")
            except HttpError as e:
                if e.resp.status in (404, 410):
                    # Was deleted, recreate
                    try:
                        new_event = gcal_client.create_event(target_cal, gcal_body)
                        event_map[uid] = {
                            "gcal_event_id": new_event["id"],
                            "calendar_id": target_cal,
                            "summary": summary,
                            "dtstart": start_iso,
                            "dtend": end_iso,
                        }
                        updated += 1
                    except Exception as e2:
                        print(f"  WARNING: Failed to recreate {summary}: {e2}")
                else:
                    print(f"  WARNING: Failed to update {summary}: {e}")
            continue

        # New event -> create
        try:
            new_event = gcal_client.create_event(target_cal, gcal_body)
            event_map[uid] = {
                "gcal_event_id": new_event["id"],
                "calendar_id": target_cal,
                "summary": summary,
                "dtstart": start_iso,
                "dtend": end_iso,
            }
            created += 1
            print(f"  Created: {summary} ({start_iso})")
        except Exception as e:
            print(f"  WARNING: Failed to create {summary}: {e}")

    # Delete events that disappeared from ICS feed
    stale_uids = [uid for uid in event_map if uid not in ics_events]
    for uid in stale_uids:
        info = event_map.pop(uid)
        _safe_delete(info["calendar_id"], info["gcal_event_id"])
        deleted += 1
        print(f"  Deleted: {info['summary']}")

    state["event_map"] = event_map
    state["last_fetch"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_state(state)

    print(f"\nDone. Created: {created}, Updated: {updated}, Deleted: {deleted}")


if __name__ == "__main__":
    run()
