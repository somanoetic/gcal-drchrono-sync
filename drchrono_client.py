"""DrChrono API client -- OAuth token management + appointment CRUD."""

import datetime
import json
import os
import time
import requests
from requests_oauthlib import OAuth2Session

import config

TOKEN_STORE = os.path.join(os.path.dirname(__file__), ".drchrono_token.json")

# Minimum delay between DrChrono API calls (seconds) to avoid rate limits
_API_CALL_DELAY = 0.5
_last_api_call = 0.0


class NotFoundError(Exception):
    """Raised when a DrChrono resource no longer exists (404)."""
    pass


class ConfigError(Exception):
    """Raised when DrChrono rejects a payload because a configured ID
    (office / patient / profile / doctor) is no longer valid.

    Carries the office_id and the parsed error body so the caller can
    surface a useful notification instead of treating it as transient.
    """
    def __init__(self, message, office_id=None, body=None):
        super().__init__(message)
        self.office_id = office_id
        self.body = body


def _load_token():
    if os.path.exists(TOKEN_STORE):
        with open(TOKEN_STORE) as f:
            return json.load(f)
    return None


def _save_token(token):
    with open(TOKEN_STORE, "w") as f:
        json.dump(token, f)


def _throttle():
    """Enforce minimum delay between API calls."""
    global _last_api_call
    elapsed = time.time() - _last_api_call
    if elapsed < _API_CALL_DELAY:
        time.sleep(_API_CALL_DELAY - elapsed)
    _last_api_call = time.time()


def _get_session():
    """Return a requests session with a valid DrChrono access token.

    Automatically refreshes if expired.
    """
    token = _load_token()
    if not token:
        raise RuntimeError(
            "No DrChrono token found. Run auth_drchrono.py first."
        )

    # Check if token needs refresh (with 60s buffer)
    expires_at = token.get("expires_at", 0)
    if time.time() > expires_at - 60:
        token = _refresh_token(token)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token['access_token']}",
        "Content-Type": "application/json",
    })
    return session


def _refresh_token(token, max_retries=8):
    """Refresh the DrChrono OAuth access token with retry on rate limit."""
    for attempt in range(max_retries + 1):
        _throttle()
        resp = requests.post(config.DRCHRONO_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": config.DRCHRONO_CLIENT_ID,
            "client_secret": config.DRCHRONO_CLIENT_SECRET,
        })
        if resp.status_code != 429:
            resp.raise_for_status()
            break
        # DrChrono puts wait time in response body, not Retry-After header
        wait = _parse_throttle_wait(resp, fallback=min(2 ** (attempt + 2), 3600))
        print(f"  Token refresh rate limited, waiting {wait}s (attempt {attempt + 1}/{max_retries + 1})...")
        time.sleep(wait)
    else:
        resp.raise_for_status()  # raise the 429 if all retries exhausted

    new_token = resp.json()
    # Compute absolute expiry
    new_token["expires_at"] = time.time() + new_token.get("expires_in", 7200)
    # Preserve refresh_token if not returned
    if "refresh_token" not in new_token:
        new_token["refresh_token"] = token["refresh_token"]
    _save_token(new_token)
    print("  DrChrono token refreshed.")
    return new_token


# -- Rate limiting -----------------------------------------------------


def _parse_throttle_wait(resp, fallback=60):
    """Extract wait time from DrChrono 429 response.

    DrChrono puts it in the JSON body: {"detail": "...Expected available in 123.0 seconds."}
    Falls back to Retry-After header, then the provided fallback.
    """
    try:
        body = resp.json()
        detail = body.get("detail", "")
        # Parse "Expected available in 2295.0 seconds."
        if "available in" in detail:
            import re
            match = re.search(r"available in ([\d.]+)", detail)
            if match:
                wait = int(float(match.group(1))) + 5  # add small buffer
                return min(wait, 3600)  # cap at 1 hour
    except Exception:
        pass
    header = resp.headers.get("Retry-After")
    if header:
        return min(int(header), 3600)
    return fallback


def _request_with_retry(session, method, url, max_retries=5, **kwargs):
    """Make a request with retry on 429 rate limiting."""
    _throttle()
    for attempt in range(max_retries + 1):
        resp = getattr(session, method)(url, **kwargs)
        if resp.status_code != 429:
            return resp
        wait = _parse_throttle_wait(resp, fallback=min(2 ** (attempt + 1), 60))
        print(f"    Rate limited, waiting {wait}s (attempt {attempt + 1}/{max_retries + 1})...")
        time.sleep(wait)
    return resp


# -- Conflict inspection -----------------------------------------------

# Per-process cache so a sync run with hundreds of 409s on the same date
# only hits /appointments once per date (was once per 409, causing rate
# limits and 60+ minute syncs).
_appts_cache_by_date = {}


def reset_classify_cache():
    """Clear the conflict-classification cache. Call between sync runs."""
    _appts_cache_by_date.clear()


def classify_conflict(scheduled_time, duration_minutes):
    """When create_break gets a 409, look up what's already in that slot.

    Returns one of:
      - "block"   — the conflicting appointment is OUR block patient. Harmless
                    redundant block; nothing to notify.
      - "patient" — a real patient appointment overlaps. The user should
                    reschedule someone.
      - "unknown" — couldn't determine (lookup failed, or no overlap found
                    despite the 409 — treat as worth notifying to be safe).

    Also returns the conflicting patient IDs as a list (empty for "block").

    Caches /appointments responses per date so repeated calls within a sync
    run don't hammer the DrChrono API.
    """
    try:
        start_dt = datetime.datetime.fromisoformat(scheduled_time)
    except ValueError:
        return "unknown", []
    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
    date_str = start_dt.date().isoformat()

    if date_str in _appts_cache_by_date:
        appts = _appts_cache_by_date[date_str]
    else:
        try:
            appts = fetch_appointments(date_str, date_str)
        except Exception:
            # Cache the empty result too — if /appointments is failing
            # (rate limit, 403), don't keep retrying it for every 409.
            _appts_cache_by_date[date_str] = []
            return "unknown", []
        _appts_cache_by_date[date_str] = appts

    block_patient_id = int(config.DRCHRONO_BLOCK_PATIENT_ID) if config.DRCHRONO_BLOCK_PATIENT_ID else None
    overlapping_patients = []
    for a in appts:
        try:
            a_start = datetime.datetime.fromisoformat(a["scheduled_time"])
            a_end = a_start + datetime.timedelta(minutes=a.get("duration", 0))
        except Exception:
            continue
        # Overlap check
        if a_start < end_dt and a_end > start_dt:
            pid = a.get("patient")
            if pid is not None and pid != block_patient_id:
                overlapping_patients.append(pid)

    if overlapping_patients:
        # De-dupe
        return "patient", list(dict.fromkeys(overlapping_patients))
    # If we found no non-block overlaps, it was a block-vs-block (or an
    # appointment we can't see). Treat as harmless.
    return "block", []


# -- Break (appointment) CRUD ------------------------------------------


def create_break(scheduled_time, duration_minutes, reason="", force=False):
    """Create a break appointment in each configured office.

    Uses the configured block patient + profile. DrChrono API requires a
    patient (patient=null returns 400), so we use a dummy patient.

    Args:
        force: if True, set allow_overlapping=true so DrChrono will accept
            the block even when a real patient appointment already occupies
            the slot. Use sparingly — the patient is NOT moved, just overlapped.

    Returns (appt_ids, config_errors):
      - appt_ids: list of created appointment IDs (one per office that accepted)
      - config_errors: list of {office_id, body} for offices that returned 400
        with a "X not valid" error (the caller should notify, but the event
        was otherwise created in the offices that did accept it).
    Skips offices where the time slot conflicts (409).
    Raises ConfigError if ALL offices returned 400.
    """
    session = _get_session()
    exam_room = int(config.DRCHRONO_EXAM_ROOM) if config.DRCHRONO_EXAM_ROOM else 1
    appt_ids = []
    config_errors = []

    for office_id in config.DRCHRONO_OFFICE_IDS:
        payload = {
            "doctor": int(config.DRCHRONO_DOCTOR_ID),
            "office": int(office_id),
            "exam_room": exam_room,
            "scheduled_time": scheduled_time,
            "duration": duration_minutes,
            "patient": int(config.DRCHRONO_BLOCK_PATIENT_ID),
            "profile": int(config.DRCHRONO_BLOCK_PROFILE_ID),
            "reason": reason,
            "allow_overlapping": bool(force),
        }
        resp = _request_with_retry(session, "post",
                                   f"{config.DRCHRONO_API_BASE}/appointments",
                                   json=payload)
        if resp.status_code == 409:
            # Overlap with existing appointment -- skip this office
            continue
        if resp.status_code == 400:
            # Config drift: an ID (office/patient/profile/doctor) is no longer
            # valid. Don't crash the whole event — try the remaining offices,
            # then either raise ConfigError (so the caller can notify) or
            # succeed with whatever offices worked.
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            config_errors.append({"office_id": office_id, "body": body})
            print(f"    400 from office {office_id}: {body}")
            continue
        resp.raise_for_status()
        appt_ids.append(resp.json()["id"])

    if not appt_ids:
        # No office accepted the appointment. If we hit any 400s, surface
        # ConfigError so the caller can notify; otherwise it was pure 409s.
        if config_errors:
            raise ConfigError(
                f"All offices rejected payload with 400: {config_errors}",
                office_id=config_errors[0]["office_id"],
                body=config_errors[0]["body"],
            )
        raise RuntimeError("409 conflict in all offices")
    return appt_ids, config_errors


def update_break(appt_ids, scheduled_time, duration_minutes, reason=""):
    """Update existing break appointments (one per office).

    Raises NotFoundError if any appointment no longer exists in DrChrono.
    """
    session = _get_session()
    payload = {
        "scheduled_time": scheduled_time,
        "duration": duration_minutes,
        "reason": reason,
    }

    for appt_id in appt_ids:
        resp = _request_with_retry(session, "patch",
                            f"{config.DRCHRONO_API_BASE}/appointments/{appt_id}",
                            json=payload)
        if resp.status_code == 404:
            raise NotFoundError(f"Appointment {appt_id} no longer exists in DrChrono")
        resp.raise_for_status()


def delete_break(appt_ids):
    """Delete break appointments (one per office)."""
    session = _get_session()
    if isinstance(appt_ids, (int, str)):
        appt_ids = [appt_ids]
    for appt_id in appt_ids:
        resp = _request_with_retry(session, "delete",
                                   f"{config.DRCHRONO_API_BASE}/appointments/{appt_id}")
        resp.raise_for_status()


# -- Appointment lookup ------------------------------------------------


def fetch_appointments(date_start, date_end, doctor_id=None):
    """Fetch appointments from the DrChrono API for a date range.

    DrChrono's /appointments endpoint rejects wide date_range queries with 400.
    Chunk the range into <=90-day windows and concatenate results.
    """
    session = _get_session()
    doc_id = doctor_id or config.DRCHRONO_DOCTOR_ID
    start = datetime.date.fromisoformat(date_start)
    end = datetime.date.fromisoformat(date_end)
    results = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + datetime.timedelta(days=89), end)
        url = f"{config.DRCHRONO_API_BASE}/appointments"
        params = {
            "doctor": int(doc_id),
            "date_range": f"{chunk_start.isoformat()}/{chunk_end.isoformat()}",
        }
        while url:
            resp = _request_with_retry(session, "get", url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            url = data.get("next")
            params = None  # params are already in the 'next' URL
        chunk_start = chunk_end + datetime.timedelta(days=1)
    return results


# -- Appointment profiles ----------------------------------------------


def fetch_appointment_profiles():
    """Fetch all appointment profiles and return a dict of id -> name."""
    session = _get_session()
    results = []
    url = f"{config.DRCHRONO_API_BASE}/appointment_profiles"
    while url:
        resp = _request_with_retry(session, "get", url)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return {p["id"]: {"name": p["name"], "is_virtual_base": bool(p.get("is_virtual_base"))} for p in results}


# -- Discovery helpers -------------------------------------------------


def get_current_doctor():
    """Get the logged-in doctor's info."""
    session = _get_session()
    resp = session.get(f"{config.DRCHRONO_API_BASE}/users/current")
    resp.raise_for_status()
    data = resp.json()
    doctor_id = data.get("doctor")

    # Fetch doctor detail for office info
    resp2 = session.get(f"{config.DRCHRONO_API_BASE}/doctors/{doctor_id}")
    resp2.raise_for_status()
    return resp2.json()


def list_offices():
    """List all offices."""
    session = _get_session()
    results = []
    url = f"{config.DRCHRONO_API_BASE}/offices"
    while url:
        resp = session.get(url)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return results
