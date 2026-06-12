"""Read-only diagnostic: test whether the ICS patient code embeds the DrChrono
patient ID as hex.

Hypothesis: an ICS summary "Appointment with SECH07DE5E80" decomposes as
  {leading alpha initials}{patient_id in hex}
i.e. int("07DE5E80", 16) == the API appointment's `patient` field.

For each ICS patient appointment we:
  1. Parse the trailing non-alpha run as hex -> candidate patient id.
  2. Find the API appointment(s) at the same scheduled_time.
  3. Report whether the hex-decoded id matches any of those appointments' patient.
  4. Print a verdict line so we can confirm the mapping holds for ALL events.

Does NOT write state. Refreshes the OAuth token if expired (can invalidate the
Actions cached token — see CLAUDE.md). Intended to run as a one-shot
workflow_dispatch job, not locally.
"""

import datetime

import config
import drchrono_client
import drchrono_to_gcal


def _split_code(code):
    """'SECH07DE5E80' -> ('SECH', '07DE5E80'). Initials = leading alpha run."""
    initials = ""
    for ch in code:
        if ch.isalpha():
            initials += ch
        else:
            break
    rest = code[len(initials):]
    return initials, rest


def main():
    today = datetime.date.today()
    start = today.isoformat()
    end = (today + datetime.timedelta(days=1)).isoformat()

    print(f"=== Fetching ICS feed ===")
    cal = drchrono_to_gcal._fetch_ics()

    ics_patient_events = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        summary = str(component.get("SUMMARY", ""))
        if not summary.startswith("Appointment with "):
            continue
        dtstart = component.get("DTSTART")
        if not dtstart:
            continue
        code = summary[len("Appointment with "):].strip()
        ics_patient_events.append({"summary": summary, "code": code,
                                   "dtstart": dtstart.dt})

    print(f"Found {len(ics_patient_events)} ICS patient appointment(s).")

    print(f"\n=== Fetching API appointments {start} -> {end} ===")
    appts = drchrono_client.fetch_appointments(start, end)
    # Index API appts by naive scheduled_time -> list of {patient, profile, id}
    by_time = {}
    for a in appts:
        sched = a.get("scheduled_time", "")
        if not sched:
            continue
        by_time.setdefault(sched, []).append(a)
    print(f"Got {len(appts)} API appointment(s).")

    print(f"\n=== Per-event hex-decode test ===")
    total = 0
    matched = 0
    not_hex = 0
    no_api = 0
    for evt in ics_patient_events:
        total += 1
        code = evt["code"]
        initials, rest = _split_code(code)

        # ICS dtstart -> naive API time string
        dt = evt["dtstart"]
        if isinstance(dt, datetime.datetime):
            naive = dt.replace(tzinfo=None)
            api_time = naive.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            api_time = str(dt)

        try:
            candidate_id = int(rest, 16)
        except ValueError:
            not_hex += 1
            print(f"  code={code!r}  rest={rest!r} NOT HEX")
            continue

        api_here = by_time.get(api_time, [])
        api_pids = [a.get("patient") for a in api_here]
        hit = candidate_id in api_pids
        if not api_here:
            no_api += 1
        if hit:
            matched += 1

        print(f"  code={code!r}  initials={initials!r}  hex={rest!r} -> "
              f"{candidate_id}  time={api_time}  "
              f"api_patients_at_time={api_pids}  MATCH={hit}")

    print(f"\n=== VERDICT ===")
    print(f"  total ICS patient events:        {total}")
    print(f"  hex-decoded id matched API patient: {matched}")
    print(f"  trailing part not hex:           {not_hex}")
    print(f"  no API appt at that time:        {no_api}")
    if total and matched == total:
        print(f"  RESULT: hex-decode mapping HOLDS for every event. Safe to use.")
    elif total and matched >= total - no_api and not_hex == 0:
        print(f"  RESULT: mapping holds wherever an API appt exists; "
              f"{no_api} event(s) had no same-time API appt to check.")
    else:
        print(f"  RESULT: mapping does NOT hold reliably. Do not use hex-decode.")


if __name__ == "__main__":
    main()
