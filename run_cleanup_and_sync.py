"""One-shot: clean up all duplicates in GCal and DrChrono, then full sync."""
import json
import sys
import datetime
import drchrono_client
import config
import gcal_client
from googleapiclient.errors import HttpError

sys.argv.append("--full")  # force full sync for all steps


def cleanup_gcal_buffers():
    """Delete ALL buffer events from Google Calendar so they get recreated fresh."""
    print("Cleaning up buffer events in Google Calendar...")
    calendar_id = config.QGENDA_CALENDAR_ID
    events, _ = gcal_client.full_sync(calendar_id)

    deleted = 0
    for event in events:
        props = event.get("extendedProperties", {}).get("private", {})
        if props.get("createdBy") == config.BUFFER_EVENT_TAG:
            try:
                gcal_client.delete_event(calendar_id, event["id"])
                deleted += 1
            except HttpError as e:
                if e.resp.status not in (404, 410):
                    print(f"  WARNING: Failed to delete buffer {event['id']}: {e}")
    print(f"  Deleted {deleted} buffer event(s).")

    # Clear the buffer state so shift_buffers recreates from scratch
    with open(config.BUFFER_STATE_FILE, "w") as f:
        json.dump({"sync_token": None, "shift_map": {}}, f, indent=2)
    print("  Reset buffer state.")


def cleanup_gcal_drchrono_events():
    """Delete ALL drchrono-to-gcal managed events from Google Calendar."""
    print("Cleaning up DrChrono->GCal events...")
    if not config.DRCHRONO_PATIENT_CALENDAR_ID or not config.DRCHRONO_OTHER_CALENDAR_ID:
        print("  Calendar IDs not configured, skipping.")
        return

    total_deleted = 0
    for cal_id in [config.DRCHRONO_PATIENT_CALENDAR_ID, config.DRCHRONO_OTHER_CALENDAR_ID]:
        try:
            events, _ = gcal_client.full_sync(cal_id)
        except Exception as e:
            print(f"  WARNING: Could not fetch events from {cal_id}: {e}")
            continue

        deleted = 0
        for event in events:
            props = event.get("extendedProperties", {}).get("private", {})
            if props.get("createdBy") == config.DRCHRONO_SYNC_TAG:
                try:
                    gcal_client.delete_event(cal_id, event["id"])
                    deleted += 1
                except HttpError as e:
                    if e.resp.status not in (404, 410):
                        print(f"  WARNING: Failed to delete {event['id']}: {e}")
        total_deleted += deleted
        print(f"  Deleted {deleted} event(s) from {cal_id}")

    # Clear the drchrono->gcal state
    with open(config.DRCHRONO_SYNC_STATE_FILE, "w") as f:
        json.dump({"event_map": {}}, f, indent=2)
    print(f"  Total deleted: {total_deleted}. Reset DrChrono->GCal state.")


def cleanup_drchrono_blocks():
    """Delete all orphaned [GCal Sync] appointments from DrChrono."""
    print("Cleaning up orphaned [GCal Sync] blocks in DrChrono...")
    today = datetime.date.today()
    date_start = (today - datetime.timedelta(days=7)).isoformat()
    date_end = (today + datetime.timedelta(days=config.SYNC_WINDOW_MONTHS * 30)).isoformat()

    all_appts = drchrono_client.fetch_appointments(date_start, date_end)
    gcal_blocks = [a for a in all_appts
                   if config.BLOCK_NOTE_PREFIX in (a.get("reason") or "")]
    print(f"  Found {len(gcal_blocks)} [GCal Sync] block(s) to delete.")

    deleted = 0
    for appt in gcal_blocks:
        appt_id = appt["id"]
        try:
            session = drchrono_client._get_session()
            resp = drchrono_client._request_with_retry(
                session, "delete",
                f"{config.DRCHRONO_API_BASE}/appointments/{appt_id}")
            if resp.status_code not in (404, 204, 200):
                resp.raise_for_status()
            deleted += 1
        except Exception as e:
            print(f"  ERROR deleting {appt_id}: {e}")
    print(f"  Deleted {deleted}/{len(gcal_blocks)} block(s).")

    # Clear the sync state
    with open(config.SYNC_STATE_FILE, "w") as f:
        json.dump({"sync_tokens": {}, "event_map": {}}, f, indent=2)
    print("  Reset GCal->DrChrono sync state.")


def main():
    # Step 0: Force token refresh and verify API access
    print("=" * 50)
    print("Step 0: Verify API access")
    print("=" * 50)
    token = drchrono_client._load_token()
    token['expires_at'] = 0
    drchrono_client._save_token(token)
    session = drchrono_client._get_session()
    resp = session.get(f'{config.DRCHRONO_API_BASE}/users/current')
    if not resp.ok:
        print(f"FATAL: API still broken ({resp.status_code}). Aborting.")
        return
    print("API access OK!")

    # Step 1: Clean up Google Calendar duplicates
    print()
    print("=" * 50)
    print("Step 1: Clean up Google Calendar")
    print("=" * 50)
    cleanup_gcal_buffers()
    print()
    cleanup_gcal_drchrono_events()

    # Step 2: Clean up DrChrono orphaned blocks
    print()
    print("=" * 50)
    print("Step 2: Clean up DrChrono")
    print("=" * 50)
    cleanup_drchrono_blocks()

    # Step 3: Full sync (shift buffers -> GCal->DrChrono -> DrChrono->GCal)
    print()
    print("=" * 50)
    print("Step 3: Full sync")
    print("=" * 50)
    import shift_buffers
    print("--- Shift buffers ---")
    shift_buffers.run()

    print()
    print("--- GCal -> DrChrono ---")
    import sync
    conflicts = sync.sync()

    print()
    print("--- DrChrono -> GCal ---")
    import drchrono_to_gcal
    drchrono_to_gcal.run()

    # Send email if any blocks failed due to conflicts
    if conflicts:
        print(f"\n{len(conflicts)} conflict(s) detected.")
        try:
            import notify
            notify.send_conflict_email(conflicts)
        except Exception as e:
            print(f"WARNING: Failed to send notification: {e}")
    else:
        print("\nAll done, no conflicts!")


if __name__ == "__main__":
    main()
