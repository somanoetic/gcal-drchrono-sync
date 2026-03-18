"""Create/update/delete buffer events around QGenda ER shifts in Google Calendar.

Usage:
    python shift_buffers.py          # incremental sync (or full on first run)
    python shift_buffers.py --full   # force full re-sync
"""

import json
import os
import sys
import datetime

from googleapiclient.errors import HttpError

import config
import gcal_client


def load_state():
    if os.path.exists(config.BUFFER_STATE_FILE):
        with open(config.BUFFER_STATE_FILE) as f:
            return json.load(f)
    return {"sync_token": None, "shift_map": {}}


def save_state(state):
    with open(config.BUFFER_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _is_shift_event(event):
    """Return True if the event is a timed QGenda shift (starts with SHIFT_PREFIX)."""
    summary = event.get("summary", "")
    if not summary.startswith(config.SHIFT_PREFIX):
        return False
    # Skip all-day events — buffers don't make sense for those
    start = event.get("start", {})
    if "date" in start and "dateTime" not in start:
        return False
    return True


def _build_buffer_body(title, start_iso, end_iso):
    """Build a Google Calendar event body for a buffer event."""
    return {
        "summary": title,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
        "extendedProperties": {
            "private": {"createdBy": config.BUFFER_EVENT_TAG}
        },
    }


def _is_overnight_shift(shift_start_iso, shift_end_iso):
    """Return True if this is a 10p-6a or 11p-7a overnight shift."""
    start = datetime.datetime.fromisoformat(shift_start_iso)
    end = datetime.datetime.fromisoformat(shift_end_iso)
    start_hour = start.hour
    end_hour = end.hour
    return (start_hour == 22 and end_hour == 6) or (start_hour == 23 and end_hour == 7)


OVERNIGHT_POST_BUFFER_MINUTES = 240  # 4 hours


def _compute_buffer_times(shift_start_iso, shift_end_iso):
    """Compute pre/post buffer start and end times.

    Overnight shifts (10p-6a, 11p-7a) get a 4-hour post buffer.
    All others get the standard BUFFER_DURATION_MINUTES.

    Returns (pre_start, pre_end, post_start, post_end) as ISO strings.
    """
    pre_delta = datetime.timedelta(minutes=config.BUFFER_DURATION_MINUTES)

    if _is_overnight_shift(shift_start_iso, shift_end_iso):
        post_delta = datetime.timedelta(minutes=OVERNIGHT_POST_BUFFER_MINUTES)
    else:
        post_delta = datetime.timedelta(minutes=config.BUFFER_DURATION_MINUTES)

    shift_start = datetime.datetime.fromisoformat(shift_start_iso)
    shift_end = datetime.datetime.fromisoformat(shift_end_iso)

    pre_start = (shift_start - pre_delta).isoformat()
    pre_end = shift_start.isoformat()
    post_start = shift_end.isoformat()
    post_end = (shift_end + post_delta).isoformat()

    return pre_start, pre_end, post_start, post_end


def _safe_delete(calendar_id, event_id):
    """Delete a buffer event, returning True on success. Swallows 404/410.

    Retries on 403 rate limit with exponential backoff.
    """
    import time
    for attempt in range(4):
        try:
            gcal_client.delete_event(calendar_id, event_id)
            return True
        except HttpError as e:
            if e.resp.status in (404, 410):
                return False
            if e.resp.status == 403 and "rateLimitExceeded" in str(e):
                wait = 2 ** (attempt + 1)
                time.sleep(wait)
                continue
            raise
    return False


def _create_buffers(calendar_id, shift_start, shift_end, existing_buffers=None):
    """Create pre and post buffer events. Returns (pre_id, post_id).

    If existing_buffers is provided, adopt matching tagged events instead
    of creating duplicates.
    """
    pre_start, pre_end, post_start, post_end = _compute_buffer_times(shift_start, shift_end)

    pre_id = None
    post_id = None

    # Try to adopt existing tagged buffer events at matching times
    if existing_buffers:
        pre_key = (pre_start, pre_end)
        post_key = (post_start, post_end)
        if pre_key in existing_buffers and existing_buffers[pre_key]:
            pre_id = existing_buffers[pre_key].pop(0)
        if post_key in existing_buffers and existing_buffers[post_key]:
            post_id = existing_buffers[post_key].pop(0)

    # Create any buffers we couldn't adopt
    if not pre_id:
        pre_body = _build_buffer_body(config.BUFFER_PRE_LABEL, pre_start, pre_end)
        pre_event = gcal_client.create_event(calendar_id, pre_body)
        pre_id = pre_event["id"]

    if not post_id:
        post_body = _build_buffer_body(config.BUFFER_POST_LABEL, post_start, post_end)
        post_event = gcal_client.create_event(calendar_id, post_body)
        post_id = post_event["id"]

    return pre_id, post_id


def _update_buffers(calendar_id, mapping, shift_start, shift_end):
    """Update existing buffer events to match new shift times.

    If a buffer was manually deleted (404), recreate it.
    Returns updated mapping dict.
    """
    pre_start, pre_end, post_start, post_end = _compute_buffer_times(shift_start, shift_end)

    # Update pre buffer
    pre_body = _build_buffer_body(config.BUFFER_PRE_LABEL, pre_start, pre_end)
    try:
        gcal_client.update_event(calendar_id, mapping["pre_buffer_id"], pre_body)
    except HttpError as e:
        if e.resp.status in (404, 410):
            pre_event = gcal_client.create_event(calendar_id, pre_body)
            mapping["pre_buffer_id"] = pre_event["id"]
        else:
            raise

    # Update post buffer
    post_body = _build_buffer_body(config.BUFFER_POST_LABEL, post_start, post_end)
    try:
        gcal_client.update_event(calendar_id, mapping["post_buffer_id"], post_body)
    except HttpError as e:
        if e.resp.status in (404, 410):
            post_event = gcal_client.create_event(calendar_id, post_body)
            mapping["post_buffer_id"] = post_event["id"]
        else:
            raise

    mapping["shift_start"] = shift_start
    mapping["shift_end"] = shift_end
    return mapping


def _delete_buffers(calendar_id, mapping):
    """Delete both buffer events for a shift."""
    _safe_delete(calendar_id, mapping["pre_buffer_id"])
    _safe_delete(calendar_id, mapping["post_buffer_id"])


def _scan_existing_buffers(calendar_id):
    """Scan GCal for tagged buffer events, indexed by (start, end) for dedup.

    Returns:
      by_time: (start_iso, end_iso) -> [event_id, ...]
      all_events: full event list (reused for old-buffer cleanup)
    """
    all_events, _ = gcal_client.full_sync(calendar_id)
    by_time = {}

    for event in all_events:
        props = event.get("extendedProperties", {}).get("private", {})
        if props.get("createdBy") != config.BUFFER_EVENT_TAG:
            continue
        start = event.get("start", {}).get("dateTime", "")
        end = event.get("end", {}).get("dateTime", "")
        if start and end:
            by_time.setdefault((start, end), []).append(event["id"])

    return by_time, all_events


def _cleanup_old_buffers(calendar_id, shift_map, all_events):
    """Delete old buffer events (e.g. from Zapier) that lack our tag.

    Deletes:
    - "Prepare for ED shift" events (old Zapier pre-buffer)
    - Untitled / "(No title)" events that are exactly 2h and adjacent to an SL shift
    Skips events we're currently tracking in shift_map.
    """
    our_buffer_ids = set()
    for mapping in shift_map.values():
        our_buffer_ids.add(mapping["pre_buffer_id"])
        our_buffer_ids.add(mapping["post_buffer_id"])

    cleaned = 0

    # Build shift boundary times from live SL events (not just state file)
    shift_boundaries = set()
    for mapping in shift_map.values():
        shift_boundaries.add(mapping["shift_start"])
        shift_boundaries.add(mapping["shift_end"])
    for event in all_events:
        if _is_shift_event(event):
            shift_boundaries.add(event["start"]["dateTime"])
            shift_boundaries.add(event["end"]["dateTime"])

    buffer_delta = datetime.timedelta(minutes=config.BUFFER_DURATION_MINUTES)

    for event in all_events:
        event_id = event.get("id")
        if event_id in our_buffer_ids:
            continue
        props = event.get("extendedProperties", {}).get("private", {})
        if props.get("createdBy") == config.BUFFER_EVENT_TAG:
            continue

        summary = event.get("summary", "")
        start = event.get("start", {})
        end = event.get("end", {})

        # 1) "Prepare for ED shift" — old Zapier pre-buffer, delete unconditionally
        if summary == "Prepare for ED shift":
            _safe_delete(calendar_id, event_id)
            cleaned += 1
            print(f"  Removed old buffer: 'Prepare for ED shift' ({start.get('dateTime', '?')})")
            continue

        # 2) "(No title)" or empty — old Zapier post-buffer
        #    Only delete if it's exactly 2h long and adjacent to an SL shift
        if summary in ("(No title)", ""):
            if "dateTime" not in start or "dateTime" not in end:
                continue
            start_dt = datetime.datetime.fromisoformat(start["dateTime"])
            end_dt = datetime.datetime.fromisoformat(end["dateTime"])
            if (end_dt - start_dt) != buffer_delta:
                continue
            if end["dateTime"] in shift_boundaries or start["dateTime"] in shift_boundaries:
                _safe_delete(calendar_id, event_id)
                cleaned += 1
                print(f"  Removed old buffer: {summary!r} ({start['dateTime']})")

    return cleaned


def _cleanup_orphaned_buffers(existing_buffers, shift_map, calendar_id):
    """Delete tagged buffer events that are not tracked in shift_map.

    Called after the main sync loop. existing_buffers entries that were
    adopted have been popped; remaining entries are orphans.
    """
    import time

    # Collect all tracked buffer IDs
    tracked_ids = set()
    for mapping in shift_map.values():
        tracked_ids.add(mapping["pre_buffer_id"])
        tracked_ids.add(mapping["post_buffer_id"])

    cleaned = 0
    for (start, _), event_ids in existing_buffers.items():
        for event_id in event_ids:
            if event_id in tracked_ids:
                continue
            if _safe_delete(calendar_id, event_id):
                cleaned += 1
            # Throttle to avoid GCal rate limits
            if cleaned % 10 == 0:
                time.sleep(1)

    return cleaned


def run():
    force_full = "--full" in sys.argv
    state = load_state()
    shift_map = state.get("shift_map", {})
    sync_token = state.get("sync_token")
    calendar_id = config.QGENDA_CALENDAR_ID

    print(f"Shift buffer sync on calendar: {calendar_id}")

    # Scan GCal for existing tagged buffer events (used for dedup + orphan cleanup)
    existing_buffers, all_events = _scan_existing_buffers(calendar_id)
    tagged_count = sum(len(v) for v in existing_buffers.values())
    if tagged_count:
        print(f"  Found {tagged_count} existing tagged buffer event(s) in GCal.")

    # Clean up old untagged buffer events (e.g. from Zapier)
    print("  Cleaning up old buffer events...")
    cleaned = _cleanup_old_buffers(calendar_id, shift_map, all_events)
    if cleaned:
        print(f"  Removed {cleaned} old buffer event(s).")

    # On --full, delete all existing buffers so they get recreated with current settings
    if force_full and shift_map:
        print(f"  Full sync: removing {len(shift_map)} existing buffer pair(s)...")
        for sid in list(shift_map):
            _delete_buffers(calendar_id, shift_map.pop(sid))
        state["sync_token"] = None
        sync_token = None

    # Decide: full or incremental
    if force_full or not sync_token:
        print("  Full sync...")
        events, new_sync_token = gcal_client.full_sync(calendar_id)
        is_full = True
    else:
        print("  Incremental sync...")
        events, new_sync_token = gcal_client.incremental_sync(calendar_id, sync_token)
        if events is None:
            print("  Sync token expired, falling back to full sync...")
            events, new_sync_token = gcal_client.full_sync(calendar_id)
            is_full = True
        else:
            is_full = False

    print(f"  Fetched {len(events)} event(s).")

    created = 0
    updated = 0
    deleted = 0
    skipped = 0
    adopted = 0
    seen_shift_ids = set()

    for event in events:
        event_id = event.get("id")
        status = event.get("status")

        # Cancelled event that we're tracking → delete buffers
        if status == "cancelled":
            if event_id in shift_map:
                mapping = shift_map.pop(event_id)
                _delete_buffers(calendar_id, mapping)
                deleted += 1
                print(f"  Deleted buffers for cancelled shift: {event_id}")
            continue

        # Not a shift event → skip
        if not _is_shift_event(event):
            skipped += 1
            continue

        seen_shift_ids.add(event_id)
        shift_start = event["start"]["dateTime"]
        shift_end = event["end"]["dateTime"]
        summary = event.get("summary", "")

        # Existing mapping → check if times changed
        if event_id in shift_map:
            mapping = shift_map[event_id]
            if mapping["shift_start"] == shift_start and mapping["shift_end"] == shift_end:
                # No change
                continue
            # Shift moved → update buffers
            try:
                shift_map[event_id] = _update_buffers(calendar_id, mapping, shift_start, shift_end)
                updated += 1
                print(f"  Updated buffers: {summary} ({shift_start})")
            except Exception as e:
                print(f"  WARNING: Failed to update buffers for {summary}: {e}")
            continue

        # New shift → adopt existing tagged buffers or create new ones
        try:
            pre_id, post_id = _create_buffers(
                calendar_id, shift_start, shift_end,
                existing_buffers=existing_buffers,
            )
            shift_map[event_id] = {
                "pre_buffer_id": pre_id,
                "post_buffer_id": post_id,
                "shift_start": shift_start,
                "shift_end": shift_end,
            }
            created += 1
            print(f"  Created buffers: {summary} ({shift_start})")
        except Exception as e:
            print(f"  WARNING: Failed to create buffers for {summary}: {e}")

    # On full sync, clean up stale mappings (shifts that disappeared)
    if is_full:
        stale_ids = [sid for sid in shift_map if sid not in seen_shift_ids]
        for sid in stale_ids:
            mapping = shift_map.pop(sid)
            _delete_buffers(calendar_id, mapping)
            deleted += 1
            print(f"  Cleaned up stale buffers: {sid}")

    # Clean up orphaned tagged buffer events (exist in GCal but not tracked)
    orphans = _cleanup_orphaned_buffers(existing_buffers, shift_map, calendar_id)
    if orphans:
        print(f"  Removed {orphans} orphaned duplicate buffer(s).")

    # Save state
    state["sync_token"] = new_sync_token
    state["shift_map"] = shift_map
    save_state(state)

    print(f"\nDone. Created: {created}, Updated: {updated}, Deleted: {deleted}, Skipped: {skipped}")


if __name__ == "__main__":
    run()
