"""One-off script to remove orphaned duplicate buffer events from Google Calendar.

Finds all events tagged with 'shift-buffer-script' that are NOT tracked in
buffer_state.json and deletes them.
"""

import json
import sys
import datetime
from dateutil.relativedelta import relativedelta
from googleapiclient.errors import HttpError

import config
import gcal_client


def main():
    # Load tracked buffer IDs from state
    with open(config.BUFFER_STATE_FILE) as f:
        state = json.load(f)
    shift_map = state.get("shift_map", {})

    tracked_ids = set()
    for mapping in shift_map.values():
        tracked_ids.add(mapping["pre_buffer_id"])
        tracked_ids.add(mapping["post_buffer_id"])

    print(f"State file tracks {len(tracked_ids)} buffer event(s) across {len(shift_map)} shift(s).")

    # Fetch ALL events from the calendar
    calendar_id = config.QGENDA_CALENDAR_ID
    all_events, _ = gcal_client.full_sync(calendar_id)
    print(f"Fetched {len(all_events)} total event(s) from calendar.")

    # Find orphaned buffer events: tagged as ours but not in state
    orphaned = []
    for event in all_events:
        props = event.get("extendedProperties", {}).get("private", {})
        if props.get("createdBy") != config.BUFFER_EVENT_TAG:
            continue
        event_id = event["id"]
        if event_id not in tracked_ids:
            orphaned.append(event)

    if not orphaned:
        print("No orphaned buffer events found. Calendar is clean.")
        return

    print(f"\nFound {len(orphaned)} orphaned buffer event(s) to delete:")
    for event in orphaned:
        summary = event.get("summary", "(no title)")
        start = event.get("start", {}).get("dateTime", "?")
        print(f"  {summary} — {start} (id: {event['id']})")

    if "--yes" not in sys.argv:
        confirm = input("\nDelete all orphaned buffers? [y/N] ")
        if confirm.strip().lower() != "y":
            print("Aborted.")
            return
    else:
        print("\n--yes flag provided, proceeding with deletion...")

    deleted = 0
    for event in orphaned:
        try:
            gcal_client.delete_event(calendar_id, event["id"])
            deleted += 1
            print(f"  Deleted: {event.get('summary', '')} ({event.get('start', {}).get('dateTime', '')})")
        except HttpError as e:
            if e.resp.status in (404, 410):
                print(f"  Already gone: {event['id']}")
            else:
                print(f"  ERROR deleting {event['id']}: {e}")

    print(f"\nDone. Deleted {deleted} orphaned buffer event(s).")


if __name__ == "__main__":
    main()
