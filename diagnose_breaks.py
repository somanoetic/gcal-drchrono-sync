"""Read-only diagnostic: figure out how breaks vs. billable appointments
look in THIS DrChrono account, and what fields control claims/billing.

Motivation: synced "block" appointments use a dummy patient
(DRCHRONO_BLOCK_PATIENT_ID) and are polluting the live claims feed. We tried
POSTing patient=null to make them true breaks and got HTTP 400
{'patient': ['This field may not be null.']} (run 27770757165) — even though
the v4 docs say breaks have a null patient. This script gathers the evidence
to resolve that contradiction WITHOUT writing anything:

  1. Do ANY real breaks (appt_is_break=true / patient=null) already exist here?
     If yes, dump their full field set — that's the ground truth for what a
     valid break looks like in this account.
  2. Dump our own block appointments (dummy patient) and their billing fields
     (billing_status, status, profile, plus any CPT/charge linkage) so billing
     can see exactly what to filter on.
  3. List which appointment 'status' / 'billing_status' values actually appear.

Does NOT write any state. WILL refresh the OAuth token if expired (which can
invalidate the GitHub Actions cached token — see CLAUDE.md). Run only when the
local token is in sync or you're ready to re-authorize Actions.
"""

import datetime
import json

import config
import drchrono_client


# Fields most likely to govern whether an appointment hits the claims feed.
BILLING_HINT_KEYS = (
    "appt_is_break",
    "patient",
    "status",
    "billing_status",
    "profile",
    "reason",
    "is_walk_in",
    "allow_overlapping",
)


def _dump(appt, keys=None):
    """Print selected keys (or all) of an appointment, sorted."""
    items = appt.items() if keys is None else ((k, appt.get(k)) for k in keys)
    for k, v in sorted(items):
        print(f"      {k} = {v!r}")


def main():
    today = datetime.date.today()
    # Look back far enough to catch existing breaks, forward to catch our blocks.
    start = (today - datetime.timedelta(days=60)).isoformat()
    end = (today + datetime.timedelta(days=120)).isoformat()

    block_patient_id = (
        int(config.DRCHRONO_BLOCK_PATIENT_ID)
        if config.DRCHRONO_BLOCK_PATIENT_ID else None
    )
    print(f"Block patient id (dummy) = {block_patient_id!r}")
    print(f"Block note prefix        = {config.BLOCK_NOTE_PREFIX!r}")

    # Read-only: map every office id -> name + archived status, so we can tell
    # what office our breaks live in vs. our two sync offices (553982/553983).
    print("\n=== Offices (read-only) ===")
    session = drchrono_client._get_session()
    url = f"{config.DRCHRONO_API_BASE}/offices"
    while url:
        resp = drchrono_client._request_with_retry(session, "get", url)
        resp.raise_for_status()
        data = resp.json()
        for o in data.get("results", []):
            print(f"  id={o.get('id')}  name={o.get('name')!r}  "
                  f"archived={o.get('archived')}")
        url = data.get("next")

    print(f"\n=== Fetching appointments {start} -> {end} ===")
    appts = drchrono_client.fetch_appointments(start, end)
    print(f"Got {len(appts)} appointments.")
    if not appts:
        print("No appointments in range; nothing to analyze.")
        return

    print("\nAll keys present on appointment objects:")
    print(f"  {sorted(appts[0].keys())}")

    # 1. Real breaks already in the account?
    breaks = [a for a in appts if a.get("appt_is_break") or a.get("patient") is None]
    print(f"\n=== Existing TRUE breaks (appt_is_break=true OR patient is None): "
          f"{len(breaks)} ===")
    for a in breaks[:5]:
        print(f"\n  break appt id={a.get('id')} time={a.get('scheduled_time')}")
        _dump(a, BILLING_HINT_KEYS)
    if breaks:
        print("\n  --> A valid break EXISTS here. Compare its fields to our block "
              "payload to see what differs (esp. profile / exam_room / patient).")
        # Dump ONE break in full so we have the exact, complete shape DrChrono
        # stored — this is the template our POST must match to be accepted.
        # Prefer a break whose reason looks like a fresh manual test, else first.
        sample = next((b for b in breaks
                       if "test" in (b.get("reason") or "").lower()), breaks[0])
        print(f"\n  --- FULL raw record of break id={sample.get('id')} "
              f"(reason={sample.get('reason')!r}) ---")
        print(json.dumps(sample, indent=2, default=str))
    else:
        print("  --> NO true breaks found. Either none are created this way here, "
              "or the account genuinely can't have them via API.")

    # 2. Our own block appointments (dummy patient + [GCal Sync] tag).
    prefix = config.BLOCK_NOTE_PREFIX
    our_blocks = [
        a for a in appts
        if a.get("patient") == block_patient_id
        or (prefix and prefix in (a.get("reason") or ""))
    ]
    print(f"\n=== OUR block appointments (dummy patient or '{prefix}' reason): "
          f"{len(our_blocks)} ===")
    for a in our_blocks[:8]:
        print(f"\n  block appt id={a.get('id')} time={a.get('scheduled_time')}")
        _dump(a, BILLING_HINT_KEYS)

    # 3. Distribution of status / billing_status across everything, so we can
    #    see whether a status value cleanly separates blocks from real claims.
    def _distribution(field):
        counts = {}
        for a in appts:
            v = a.get(field, "<missing>")
            counts[repr(v)] = counts.get(repr(v), 0) + 1
        return counts

    for field in ("status", "billing_status", "appt_is_break"):
        print(f"\n=== Distribution of '{field}' across all {len(appts)} appts ===")
        for val, n in sorted(_distribution(field).items(), key=lambda x: -x[1]):
            print(f"  {val:<24} {n}")

    print("\nDone. Nothing was written.")


if __name__ == "__main__":
    main()
