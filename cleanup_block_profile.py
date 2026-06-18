"""Delete the dummy-patient block appointments that pollute the claims feed.

These were ALL created with the block appointment profile
(DRCHRONO_BLOCK_PROFILE_ID, e.g. 969445), so we filter on PROFILE rather than
the [GCal Sync] reason tag — the profile is the reliable signal (some blocks,
e.g. "Dr. Hadfield unavailable", may not carry the reason tag).

SAFETY:
  - Filters to appointments whose `profile` == the block profile AND which have
    a non-null patient (i.e. the OLD dummy-patient blocks). True breaks
    (patient=null) are skipped — we don't want to delete the good new ones.
  - Prints a histogram of reasons + a per-appointment list so you can eyeball
    the set before deleting.
  - Dry run by default. Pass --delete to actually remove.

Usage:
    python cleanup_block_profile.py            # dry run (list only)
    python cleanup_block_profile.py --delete   # actually delete
"""

import sys
import datetime
from collections import Counter

import config
import drchrono_client


def main():
    dry_run = "--delete" not in sys.argv
    if dry_run:
        print("DRY RUN -- pass --delete to actually remove appointments\n")

    profile_id = config.DRCHRONO_BLOCK_PROFILE_ID
    if not profile_id:
        print("ERROR: DRCHRONO_BLOCK_PROFILE_ID not configured")
        return
    profile_id = int(profile_id)
    print(f"Target block profile id = {profile_id}")

    today = datetime.date.today()
    # Wide window: back 90 days, forward the full sync window, to catch all.
    date_start = (today - datetime.timedelta(days=90)).isoformat()
    date_end = (today + datetime.timedelta(days=config.SYNC_WINDOW_MONTHS * 30)).isoformat()
    print(f"Fetching appointments {date_start} -> {date_end}...")
    all_appts = drchrono_client.fetch_appointments(date_start, date_end)
    print(f"  {len(all_appts)} appointments total in range.\n")

    # OLD dummy-patient blocks: our block profile AND a real (non-null) patient.
    # (True breaks have patient=null and we must NOT delete those.)
    targets = [
        a for a in all_appts
        if a.get("profile") == profile_id and a.get("patient") is not None
    ]
    # Safety: also report anything on this profile WITHOUT a patient, so we can
    # see if the filter is excluding things we'd expect.
    on_profile_breaks = [
        a for a in all_appts
        if a.get("profile") == profile_id and a.get("patient") is None
    ]

    print(f"=== {len(targets)} dummy-patient blocks on profile {profile_id} "
          f"(deletion targets) ===")
    reasons = Counter((a.get("reason") or "(no reason)") for a in targets)
    print("Reason histogram:")
    for reason, n in reasons.most_common():
        print(f"  {n:>4}  {reason}")
    print(f"\n(Also {len(on_profile_breaks)} patient=null appts on this profile "
          f"— NOT targeted.)\n")

    if dry_run:
        print("Sample (first 15):")
        for a in targets[:15]:
            print(f"  {a.get('scheduled_time')}  office={a.get('office')}  "
                  f"patient={a.get('patient')}  {a.get('reason')!r}  (id={a.get('id')})")
        print(f"\nWould delete {len(targets)} appointment(s). "
              f"Run with --delete to proceed.")
        return

    deleted = 0
    failed = 0
    for a in targets:
        appt_id = a["id"]
        try:
            session = drchrono_client._get_session()
            resp = drchrono_client._request_with_retry(
                session, "delete",
                f"{config.DRCHRONO_API_BASE}/appointments/{appt_id}")
            if resp.status_code == 404:
                print(f"  {appt_id}: already gone")
            else:
                resp.raise_for_status()
            deleted += 1
        except Exception as e:
            failed += 1
            print(f"  ERROR deleting {appt_id}: {e}")
    print(f"\nDeleted {deleted}/{len(targets)} ({failed} failed).")


if __name__ == "__main__":
    main()
