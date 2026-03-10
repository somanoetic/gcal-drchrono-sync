"""Delete all orphaned [GCal Sync] appointments from DrChrono.

Use this when the sync_state event_map gets wiped but old appointments
still exist in DrChrono, causing 409 conflicts on re-sync.

Usage:
    python cleanup_orphaned_blocks.py          # dry run (list only)
    python cleanup_orphaned_blocks.py --delete  # actually delete
"""

import sys
import datetime
import config
import drchrono_client


def main():
    dry_run = "--delete" not in sys.argv

    if dry_run:
        print("DRY RUN -- pass --delete to actually remove appointments")
        print()

    patient_id = config.DRCHRONO_BLOCK_PATIENT_ID
    if not patient_id:
        print("ERROR: DRCHRONO_BLOCK_PATIENT_ID not configured")
        return

    # Fetch appointments across the sync window
    today = datetime.date.today()
    date_start = (today - datetime.timedelta(days=7)).isoformat()
    date_end = (today + datetime.timedelta(days=config.SYNC_WINDOW_MONTHS * 30)).isoformat()
    print(f"Fetching appointments from {date_start} to {date_end}...")

    all_appts = drchrono_client.fetch_appointments(date_start, date_end)

    # Filter to [GCal Sync] blocks
    gcal_blocks = [a for a in all_appts
                   if config.BLOCK_NOTE_PREFIX in (a.get("reason") or "")]

    print(f"Found {len(gcal_blocks)} [GCal Sync] appointment(s) out of {len(all_appts)} total.")
    print()

    deleted = 0
    for appt in gcal_blocks:
        appt_id = appt["id"]
        reason = appt.get("reason", "")
        scheduled = appt.get("scheduled_time", "")
        office = appt.get("office", "")
        print(f"  {scheduled}  office={office}  {reason}  (id={appt_id})")

        if not dry_run:
            try:
                # Get a fresh session for each delete to avoid token expiry
                session = drchrono_client._get_session()
                resp = drchrono_client._request_with_retry(
                    session, "delete",
                    f"{config.DRCHRONO_API_BASE}/appointments/{appt_id}")
                if resp.status_code == 404:
                    print(f"    Already deleted")
                else:
                    resp.raise_for_status()
                deleted += 1
            except Exception as e:
                print(f"    ERROR deleting: {e}")

    if dry_run:
        print()
        print(f"Would delete {len(gcal_blocks)} appointment(s). Run with --delete to proceed.")
    else:
        print()
        print(f"Deleted {deleted}/{len(gcal_blocks)} appointment(s).")


if __name__ == "__main__":
    main()
