"""Main sync orchestrator — Google Calendar → DrChrono breaks.

Usage:
    python sync.py          # normal sync (incremental if possible)
    python sync.py --full   # force a full re-sync
"""

import json
import os
import sys
import datetime

import config
import gcal_client
import drchrono_client


def load_state():
    if os.path.exists(config.SYNC_STATE_FILE):
        with open(config.SYNC_STATE_FILE) as f:
            return json.load(f)
    return {"sync_tokens": {}, "event_map": {}}


def save_state(state):
    with open(config.SYNC_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _allday_matches_keywords(summary):
    """Check if an all-day event summary matches any allowed keywords."""
    lower = summary.lower()
    return any(kw in lower for kw in config.ALLDAY_KEYWORDS)


def _expand_allday(event):
    """Expand an all-day event into per-day (scheduled_time, duration, summary, sub_key) tuples.

    Each day gets a block covering business hours (ALLDAY_BLOCK_START to ALLDAY_BLOCK_END).
    sub_key is the date string, used to make event_map keys unique per day.
    """
    summary = event.get("summary", "Busy")
    if not _allday_matches_keywords(summary):
        return []

    start_date = datetime.date.fromisoformat(event["start"]["date"])
    end_date = datetime.date.fromisoformat(event["end"]["date"])  # exclusive

    sh, sm = (int(x) for x in config.ALLDAY_BLOCK_START.split(":"))
    eh, em = (int(x) for x in config.ALLDAY_BLOCK_END.split(":"))
    block_start = datetime.time(sh, sm)
    block_end = datetime.time(eh, em)
    duration = int((datetime.datetime.combine(start_date, block_end)
                     - datetime.datetime.combine(start_date, block_start)).total_seconds() / 60)

    blocks = []
    current = start_date
    while current < end_date:
        st = datetime.datetime.combine(current, block_start).strftime("%Y-%m-%dT%H:%M:%S")
        blocks.append((st, duration, summary, current.isoformat()))
        current += datetime.timedelta(days=1)

    return blocks


def parse_event(event):
    """Parse a Google Calendar event into a list of blocks to sync.

    Returns a list of (scheduled_time, duration_minutes, summary, sub_key) tuples.
    sub_key is "" for timed events, or a date string for individual days of all-day events.
    Returns empty list if the event should be skipped.
    """
    start = event.get("start", {})
    end = event.get("end", {})

    # All-day event
    if "date" in start and "dateTime" not in start:
        return _expand_allday(event)

    # Timed event
    start_dt = datetime.datetime.fromisoformat(start["dateTime"])
    end_dt = datetime.datetime.fromisoformat(end["dateTime"])
    duration = int((end_dt - start_dt).total_seconds() / 60)

    if duration <= 0:
        return []

    scheduled_time = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
    summary = event.get("summary", "Busy")

    return [(scheduled_time, duration, summary, "")]


def make_note(summary):
    return f"{config.BLOCK_NOTE_PREFIX} {summary}"


def _map_key(event_id, sub_key):
    """Build the event_map key. Plain event_id for timed events,
    event_id__day__YYYY-MM-DD for individual days of all-day events."""
    if sub_key:
        return f"{event_id}__day__{sub_key}"
    return event_id


def _sync_calendar(calendar_id, state, force_full):
    """Sync one Google Calendar into DrChrono. Mutates state in place."""
    sync_tokens = state.setdefault("sync_tokens", {})
    event_map = state.setdefault("event_map", {})
    sync_token = sync_tokens.get(calendar_id)

    print(f"\n  Calendar: {calendar_id}")

    # ── Decide: full or incremental ─────────────────────────────────
    if force_full or not sync_token:
        print("    Full sync...")
        events, new_sync_token = gcal_client.full_sync(calendar_id)
        is_full = True
    else:
        print("    Incremental sync...")
        events, new_sync_token = gcal_client.incremental_sync(calendar_id, sync_token)
        if events is None:
            print("    Sync token expired, falling back to full sync...")
            events, new_sync_token = gcal_client.full_sync(calendar_id)
            is_full = True
        else:
            is_full = False

    print(f"    Fetched {len(events)} event(s).")

    created = 0
    updated = 0
    deleted = 0
    skipped = 0
    seen_keys = set()

    for event in events:
        event_id = event.get("id")
        status = event.get("status")

        # ── Deleted event ───────────────────────────────────────────
        if status == "cancelled":
            # Could be a timed event or an expanded all-day — check both patterns
            keys_to_delete = [k for k in event_map if k == event_id or k.startswith(f"{event_id}__day__")]
            for key in keys_to_delete:
                block_ids = event_map.pop(key)
                try:
                    drchrono_client.delete_break(block_ids)
                    deleted += 1
                    print(f"    Deleted block: {key}")
                except Exception as e:
                    print(f"    WARNING: Failed to delete block {block_ids}: {e}")
            continue

        # ── Parse event into blocks ─────────────────────────────────
        blocks = parse_event(event)
        if not blocks:
            skipped += 1
            continue

        # Track which map keys this event produces (for stale cleanup)
        block_keys = set()
        for scheduled_time, duration, summary, sub_key in blocks:
            key = _map_key(event_id, sub_key)
            block_keys.add(key)
            seen_keys.add(key)
            reason = make_note(summary)

            # ── Existing mapping → update ───────────────────────────
            if key in event_map:
                appt_ids = event_map[key]
                try:
                    drchrono_client.update_break(appt_ids, scheduled_time, duration, reason)
                    updated += 1
                    print(f"    Updated: {summary} ({scheduled_time}, {duration}m)")
                except Exception as e:
                    print(f"    WARNING: Failed to update {appt_ids}: {e}")
                continue

            # ── New → create ────────────────────────────────────────
            try:
                appt_ids = drchrono_client.create_break(scheduled_time, duration, reason)
                event_map[key] = appt_ids
                created += 1
                print(f"    Created: {summary} ({scheduled_time}, {duration}m)")
            except Exception as e:
                print(f"    WARNING: Failed to create block for '{summary}': {e}")

        # If an all-day event was shortened (fewer days), clean up extra day blocks
        old_day_keys = [k for k in event_map
                        if k.startswith(f"{event_id}__day__") and k not in block_keys]
        for key in old_day_keys:
            block_ids = event_map.pop(key)
            try:
                drchrono_client.delete_break(block_ids)
                deleted += 1
                print(f"    Removed day block: {key}")
            except Exception as e:
                print(f"    WARNING: Failed to remove day block {block_ids}: {e}")

    # ── On full sync, clean up stale mappings for this calendar ─────
    if is_full:
        current_event_ids = {e["id"] for e in events if e.get("status") != "cancelled"}
        stale = [k for k in list(event_map.keys())
                 if k not in seen_keys
                 and k.split("__day__")[0] not in current_event_ids
                 ]
        if len(config.GOOGLE_CALENDAR_IDS) == 1 or force_full:
            for key in stale:
                block_ids = event_map.pop(key)
                try:
                    drchrono_client.delete_break(block_ids)
                    deleted += 1
                    print(f"    Cleaned up stale block: {key}")
                except Exception as e:
                    print(f"    WARNING: Failed to clean up block {appt_id}: {e}")

    sync_tokens[calendar_id] = new_sync_token
    return created, updated, deleted, skipped


def sync():
    force_full = "--full" in sys.argv

    state = load_state()

    # Migrate old state format (single sync_token → per-calendar)
    if "sync_token" in state and "sync_tokens" not in state:
        old_token = state.pop("sync_token")
        state["sync_tokens"] = {}
        if old_token:
            # Assume old token was for "primary"
            state["sync_tokens"]["primary"] = old_token

    total_created = 0
    total_updated = 0
    total_deleted = 0
    total_skipped = 0

    print(f"Syncing {len(config.GOOGLE_CALENDAR_IDS)} calendar(s)...")

    for cal_id in config.GOOGLE_CALENDAR_IDS:
        c, u, d, s = _sync_calendar(cal_id, state, force_full)
        total_created += c
        total_updated += u
        total_deleted += d
        total_skipped += s

    save_state(state)

    print(f"\nDone. Created: {total_created}, Updated: {total_updated}, "
          f"Deleted: {total_deleted}, Skipped: {total_skipped}")


if __name__ == "__main__":
    sync()
