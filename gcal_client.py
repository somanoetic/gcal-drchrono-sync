"""Google Calendar client with incremental sync support."""

import os
import datetime
from dateutil.relativedelta import relativedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_credentials():
    """Load or refresh Google OAuth credentials."""
    creds = None
    if os.path.exists(config.GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(config.GOOGLE_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GOOGLE_CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(config.GOOGLE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def _build_service():
    creds = _get_credentials()
    return build("calendar", "v3", credentials=creds)


def full_sync(calendar_id):
    """Fetch all events from now to SYNC_WINDOW_MONTHS ahead for one calendar.

    Returns (events, next_sync_token).
    """
    service = _build_service()

    now = datetime.datetime.now(datetime.timezone.utc)
    time_max = now + relativedelta(months=config.SYNC_WINDOW_MONTHS)

    all_events = []
    page_token = None

    while True:
        resp = service.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            pageToken=page_token,
        ).execute()

        all_events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    sync_token = resp.get("nextSyncToken")
    return all_events, sync_token


def incremental_sync(calendar_id, sync_token):
    """Fetch only changes since the last sync for one calendar.

    Returns (events, new_sync_token).
    If the syncToken is invalidated (410 Gone), returns None to signal a full re-sync.
    """
    service = _build_service()

    all_events = []
    page_token = None

    while True:
        try:
            resp = service.events().list(
                calendarId=calendar_id,
                syncToken=sync_token,
                pageToken=page_token,
            ).execute()
        except Exception as e:
            # Google returns 410 when syncToken is expired
            if hasattr(e, "resp") and e.resp.status == 410:
                return None, None
            raise

        all_events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    new_sync_token = resp.get("nextSyncToken")
    return all_events, new_sync_token


def create_event(calendar_id, body):
    """Create an event in the given calendar. Returns the created event."""
    service = _build_service()
    return service.events().insert(calendarId=calendar_id, body=body).execute()


def update_event(calendar_id, event_id, body):
    """Update an existing event. Returns the updated event."""
    service = _build_service()
    return service.events().update(
        calendarId=calendar_id, eventId=event_id, body=body
    ).execute()


def delete_event(calendar_id, event_id):
    """Delete an event from the given calendar."""
    service = _build_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()


def search_events(calendar_id, query, time_min=None, time_max=None):
    """Search for events matching a text query. Returns list of events."""
    service = _build_service()

    if time_min is None:
        time_min = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if time_max is None:
        time_max = (datetime.datetime.now(datetime.timezone.utc)
                    + relativedelta(months=config.SYNC_WINDOW_MONTHS)).isoformat()

    all_events = []
    page_token = None

    while True:
        resp = service.events().list(
            calendarId=calendar_id,
            q=query,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            pageToken=page_token,
        ).execute()

        all_events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return all_events


def find_or_create_calendar(name):
    """Find an existing secondary calendar by name, or create it. Returns the calendar ID."""
    service = _build_service()

    # Check existing calendars
    page_token = None
    while True:
        resp = service.calendarList().list(pageToken=page_token).execute()
        for entry in resp.get("items", []):
            if entry.get("summary") == name:
                return entry["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Not found — create it
    new_cal = service.calendars().insert(body={"summary": name}).execute()
    print(f"  Created Google Calendar: {name} ({new_cal['id']})")
    return new_cal["id"]
