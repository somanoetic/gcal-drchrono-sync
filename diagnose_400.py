"""One-shot diagnostic: figure out why /appointments POST returns 400.

Verifies each configured ID still exists in DrChrono and prints the actual
400 response body from a dry POST attempt.

Read-only except for one POST that we expect to fail. The POST is in the
distant future and uses a tiny duration, so even if it somehow succeeds
the block is harmless and easy to spot.
"""

import datetime
import json

import config
import drchrono_client


def _section(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def _check_get(label, url, session):
    try:
        resp = session.get(url)
        print(f"{label}: HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            # /offices and /appointment_profiles return paginated lists;
            # individual /patients/{id}, /doctors/{id}, /appointment_profiles/{id} return objects
            if isinstance(data, dict) and "results" in data:
                print(f"  count: {len(data['results'])}")
                for item in data["results"]:
                    name = item.get("name") or item.get("first_name", "") + " " + item.get("last_name", "")
                    print(f"    id={item.get('id')}  name={name!r}")
            else:
                # Single object
                name = data.get("name") or (data.get("first_name", "") + " " + data.get("last_name", "")).strip()
                print(f"  id={data.get('id')}  name={name!r}")
                # Useful fields for the block patient/profile
                for k in ("is_virtual_base", "duration", "is_active", "is_deleted", "status"):
                    if k in data:
                        print(f"  {k}={data[k]}")
        else:
            try:
                print(f"  body: {resp.json()}")
            except Exception:
                print(f"  body: {resp.text[:500]}")
    except Exception as e:
        print(f"{label}: EXCEPTION {e}")


def main():
    print(f"Configured values from .env:")
    print(f"  DRCHRONO_DOCTOR_ID        = {config.DRCHRONO_DOCTOR_ID}")
    print(f"  DRCHRONO_OFFICE_IDS       = {config.DRCHRONO_OFFICE_IDS}")
    print(f"  DRCHRONO_EXAM_ROOM        = {config.DRCHRONO_EXAM_ROOM}")
    print(f"  DRCHRONO_BLOCK_PATIENT_ID = {config.DRCHRONO_BLOCK_PATIENT_ID}")
    print(f"  DRCHRONO_BLOCK_PROFILE_ID = {config.DRCHRONO_BLOCK_PROFILE_ID}")

    session = drchrono_client._get_session()
    base = config.DRCHRONO_API_BASE

    _section("1. Doctor lookup")
    _check_get(f"GET /doctors/{config.DRCHRONO_DOCTOR_ID}",
               f"{base}/doctors/{config.DRCHRONO_DOCTOR_ID}", session)

    _section("2. Offices — list all to see which IDs are real")
    _check_get("GET /offices", f"{base}/offices", session)

    _section("3. Each configured office ID")
    for office_id in config.DRCHRONO_OFFICE_IDS:
        _check_get(f"GET /offices/{office_id}", f"{base}/offices/{office_id}", session)

    _section("4. Block patient lookup")
    _check_get(f"GET /patients/{config.DRCHRONO_BLOCK_PATIENT_ID}",
               f"{base}/patients/{config.DRCHRONO_BLOCK_PATIENT_ID}", session)

    _section("5. Block profile lookup")
    _check_get(f"GET /appointment_profiles/{config.DRCHRONO_BLOCK_PROFILE_ID}",
               f"{base}/appointment_profiles/{config.DRCHRONO_BLOCK_PROFILE_ID}", session)

    _section("6. Dry POST to /appointments — show the actual 400 body")
    # Pick a time far in the future so we don't collide with anything real.
    far_future = (datetime.datetime.now() + datetime.timedelta(days=365)).replace(
        hour=3, minute=0, second=0, microsecond=0
    )
    scheduled = far_future.strftime("%Y-%m-%dT%H:%M:%S")

    for office_id in config.DRCHRONO_OFFICE_IDS:
        payload = {
            "doctor": int(config.DRCHRONO_DOCTOR_ID),
            "office": int(office_id),
            "exam_room": int(config.DRCHRONO_EXAM_ROOM) if config.DRCHRONO_EXAM_ROOM else 1,
            "scheduled_time": scheduled,
            "duration": 15,
            "patient": int(config.DRCHRONO_BLOCK_PATIENT_ID),
            "profile": int(config.DRCHRONO_BLOCK_PROFILE_ID),
            "reason": "[GCal Sync] DIAGNOSTIC — delete me",
        }
        print(f"\nPOST /appointments (office={office_id}):")
        print(f"  payload: {json.dumps(payload)}")
        resp = session.post(f"{base}/appointments", json=payload)
        print(f"  status: {resp.status_code}")
        try:
            print(f"  body:   {json.dumps(resp.json(), indent=2)}")
        except Exception:
            print(f"  body:   {resp.text[:1000]}")

        # If it somehow succeeded, delete it immediately
        if resp.status_code in (200, 201):
            appt_id = resp.json().get("id")
            if appt_id:
                print(f"  CLEANUP: deleting test appointment {appt_id}")
                del_resp = session.delete(f"{base}/appointments/{appt_id}")
                print(f"  cleanup status: {del_resp.status_code}")


if __name__ == "__main__":
    main()
