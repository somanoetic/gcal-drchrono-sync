"""One-shot: list every Google Calendar the OAuth token can see.

Read-only. Used to find the calendar ID for a named calendar (e.g. 'Family')
so it can be added to GOOGLE_CALENDAR_IDS in the ENV_FILE secret.
"""

import gcal_client


def main():
    service = gcal_client._build_service()

    print(f"{'Name':<50} {'Access':<10} {'ID'}")
    print("-" * 130)

    page_token = None
    rows = []
    while True:
        resp = service.calendarList().list(pageToken=page_token).execute()
        for entry in resp.get("items", []):
            rows.append({
                "name": entry.get("summary", "(unnamed)"),
                "access": entry.get("accessRole", "?"),
                "id": entry.get("id", "?"),
                "primary": entry.get("primary", False),
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Sort so primary first, then alphabetical
    rows.sort(key=lambda r: (not r["primary"], r["name"].lower()))

    for r in rows:
        marker = " (primary)" if r["primary"] else ""
        print(f"{r['name'] + marker:<50} {r['access']:<10} {r['id']}")

    print(f"\nTotal: {len(rows)} calendar(s)")


if __name__ == "__main__":
    main()
