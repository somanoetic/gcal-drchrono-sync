"""One-time DrChrono OAuth setup helper.

Run this script to:
1. Complete the OAuth2 authorization flow
2. Save the access/refresh tokens
3. Auto-discover doctor ID, office ID, and exam room
4. Print the values to add to .env
"""

import json
import os
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests

import config
import drchrono_client

REDIRECT_PORT = 8080
auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = parse_qs(urlparse(self.path).query)
        auth_code = query.get("code", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<h2>Authorization complete!</h2><p>You can close this tab.</p>"
        )

    def log_message(self, format, *args):
        pass  # suppress noisy logs


def run():
    if not config.DRCHRONO_CLIENT_ID or not config.DRCHRONO_CLIENT_SECRET:
        print("ERROR: Set DRCHRONO_CLIENT_ID and DRCHRONO_CLIENT_SECRET in .env first.")
        return

    # Step 1: Open browser for authorization
    auth_url = (
        f"{config.DRCHRONO_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={config.DRCHRONO_CLIENT_ID}"
        f"&redirect_uri={config.DRCHRONO_REDIRECT_URI}"
        # Omit scope to request ALL scopes
        # f"&scope=..."
    )
    print(f"Opening browser for DrChrono authorization...")
    webbrowser.open(auth_url)

    # Step 2: Wait for the callback
    server = HTTPServer(("localhost", REDIRECT_PORT), CallbackHandler)
    print(f"Waiting for callback on http://localhost:{REDIRECT_PORT}/callback ...")
    while auth_code is None:
        server.handle_request()

    print(f"Got authorization code.")

    # Step 3: Exchange code for tokens
    resp = requests.post(config.DRCHRONO_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": config.DRCHRONO_CLIENT_ID,
        "client_secret": config.DRCHRONO_CLIENT_SECRET,
        "redirect_uri": config.DRCHRONO_REDIRECT_URI,
    })
    resp.raise_for_status()
    token = resp.json()
    token["expires_at"] = time.time() + token.get("expires_in", 7200)
    drchrono_client._save_token(token)
    print("Tokens saved.\n")

    # Step 4: Auto-discover IDs
    print("Discovering doctor, office, and exam room...")
    session = drchrono_client._get_session()

    # Get doctor ID from current user
    resp = session.get(f"{config.DRCHRONO_API_BASE}/users/current")
    resp.raise_for_status()
    doctor_id = resp.json().get("doctor")
    print(f"  Doctor ID: {doctor_id}")

    # Get offices directly (doesn't require doctor scope)
    office_id = ""
    exam_room = ""
    try:
        offices = drchrono_client.list_offices()
        if offices:
            office = offices[0]
            office_id = office.get("id", "")
            exam_rooms = office.get("exam_rooms", [])
            exam_room = exam_rooms[0]["index"] if exam_rooms else 0
            print(f"  Office ID: {office_id}")
            print(f"  Exam Room: {exam_room}")
        else:
            print("  WARNING: No offices found. Set DRCHRONO_OFFICE_ID manually.")
    except Exception as e:
        print(f"  WARNING: Could not fetch offices ({e}). Set DRCHRONO_OFFICE_ID manually.")

    print("\n--- Add these to your .env ---")
    print(f"DRCHRONO_DOCTOR_ID={doctor_id}")
    print(f"DRCHRONO_OFFICE_ID={office_id}")
    print(f"DRCHRONO_EXAM_ROOM={exam_room}")
    print("---")


if __name__ == "__main__":
    run()
