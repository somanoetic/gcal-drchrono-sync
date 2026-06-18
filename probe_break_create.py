"""ONE-SHOT probe: find the EXACT payload DrChrono accepts for a true break
in a sync office (553982). Our sync POST gets 400 {'patient': 'may not be null'}
even with exam_room=0 + patient=null + no profile, yet a UI-created break in the
SAME office exists fine. This tests several payload variants to find the diff.

Each variant that succeeds is immediately DELETED. Far-future slot (2030) so
nothing collides. Read-only office dump included.
"""

import json
import copy

import config
import drchrono_client

TEST_TIME = "2030-01-06T06:00:00"
TEST_DURATION = 30
TEST_OFFICE = 553982

BASE = {
    "doctor": int(config.DRCHRONO_DOCTOR_ID),
    "office": TEST_OFFICE,
    "scheduled_time": TEST_TIME,
    "duration": TEST_DURATION,
    "reason": "[PROBE] delete me",
}


def variants():
    # V1: patient=null + exam_room=0 + no profile (what master sends now)
    v1 = dict(BASE, exam_room=0, patient=None)
    # V2: same but OMIT patient key entirely (maybe null-in-json is the issue)
    v2 = dict(BASE, exam_room=0)
    # V3: omit exam_room too (UI break had exam_room=0; maybe must be absent)
    v3 = dict(BASE, patient=None)
    # V4: patient=null + exam_room=0 + explicit profile=None
    v4 = dict(BASE, exam_room=0, patient=None, profile=None)
    # V5: add status='' and allow_overlapping like the full UI record
    v5 = dict(BASE, exam_room=0, patient=None, status="", allow_overlapping=False)
    return [
        ("V1 master payload (patient=None, exam_room=0)", v1),
        ("V2 omit patient key (exam_room=0)", v2),
        ("V3 omit exam_room (patient=None)", v3),
        ("V4 explicit profile=None", v4),
        ("V5 + status='' + allow_overlapping", v5),
    ]


def main():
    session = drchrono_client._get_session()
    print("=== Offices ===")
    url = f"{config.DRCHRONO_API_BASE}/offices"
    while url:
        r = drchrono_client._request_with_retry(session, "get", url)
        r.raise_for_status()
        d = r.json()
        for o in d.get("results", []):
            print(f"  id={o.get('id')} name={o.get('name')!r} archived={o.get('archived')}")
        url = d.get("next")

    created_ids = []
    for label, payload in variants():
        print(f"\n=== {label} ===")
        print(f"payload = {json.dumps(payload)}")
        resp = drchrono_client._request_with_retry(
            session, "post", f"{config.DRCHRONO_API_BASE}/appointments", json=payload)
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        print(f"HTTP {resp.status_code}  body={json.dumps(body, default=str)[:300]}")
        if resp.status_code in (200, 201):
            aid = body.get("id")
            print(f"  --> ACCEPTED. id={aid}, appt_is_break={body.get('appt_is_break')!r}")
            created_ids.append(aid)

    # Clean up everything we created.
    for aid in created_ids:
        dr = drchrono_client._request_with_retry(
            session, "delete", f"{config.DRCHRONO_API_BASE}/appointments/{aid}")
        print(f"cleanup DELETE {aid} -> {dr.status_code}")

    print(f"\nDone. Created+deleted {len(created_ids)} test appt(s).")


if __name__ == "__main__":
    main()
