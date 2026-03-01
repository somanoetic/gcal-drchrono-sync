# Google Calendar to DrChrono Blocker Sync — Setup Guide

This tool syncs your personal Google Calendar events into DrChrono as unavailable time blocks (breaks), preventing double-booking.

## Prerequisites

- Python 3.10+
- A DrChrono account with API access
- A Google Cloud project with Calendar API enabled

---

## Step 1: Install Dependencies

```bash
cd gcal-drchrono-sync
pip install -r requirements.txt
```

## Step 2: Google Calendar Credentials

You should already have a `credentials.json` from Google Cloud Console. Place it in this directory.

If you need to create one:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create/select a project
3. Enable the **Google Calendar API**
4. Go to **Credentials** → **Create Credentials** → **OAuth client ID**
5. Application type: **Desktop app**
6. Download the JSON and save as `credentials.json` in this directory

## Step 3: DrChrono API Credentials

1. Log into DrChrono → **Account Settings** → **API**
2. Create a new API application
3. Set the redirect URI to `http://localhost:8080/callback`
4. Note the **Client ID** and **Client Secret**
5. Required scope: `calendar`

## Step 4: Configure .env

Copy the example and fill in your DrChrono credentials:

```bash
cp .env.example .env
```

Edit `.env` and set:
```
DRCHRONO_CLIENT_ID=your_client_id
DRCHRONO_CLIENT_SECRET=your_client_secret
```

## Step 5: Authorize DrChrono

Run the one-time authorization helper:

```bash
python auth_drchrono.py
```

This will:
- Open your browser for DrChrono OAuth consent
- Save tokens locally
- Auto-discover your Doctor ID, Office ID, and Exam Room
- Print the values — add them to your `.env`

## Step 6: Authorize Google Calendar

The first time you run the sync, it will open a browser for Google OAuth. This is automatic.

## Step 7: Run the Sync

```bash
python sync.py
```

First run does a full sync (all events for the next 6 months). Subsequent runs use incremental sync (only changes).

Force a full re-sync:
```bash
python sync.py --full
```

## Step 8: Schedule Automatic Runs (Optional)

### Windows Task Scheduler

1. Open Task Scheduler
2. Create Basic Task → name it "GCal DrChrono Sync"
3. Trigger: Daily, repeat every 5 minutes
4. Action: Start a program
   - Program: `python`
   - Arguments: `sync.py`
   - Start in: `C:\Users\hadfi\General Claude\gcal-drchrono-sync`
5. Check "Run whether user is logged on or not"

---

## How It Works

- **Google Calendar events** are read using the Calendar API with incremental sync (`syncToken`)
- Each event is mapped to a **DrChrono break** (appointment with no patient)
- The mapping is stored in `sync_state.json`
- On each run, only changed/new/deleted events are processed
- All-day events are skipped (they'd block the entire day)

## Files

| File | Purpose |
|------|---------|
| `sync.py` | Main entry point — orchestrates the sync |
| `gcal_client.py` | Google Calendar API reader |
| `drchrono_client.py` | DrChrono API client (OAuth + CRUD) |
| `auth_drchrono.py` | One-time DrChrono OAuth setup |
| `config.py` | Configuration loader |
| `sync_state.json` | Persisted sync state (auto-created) |
| `.drchrono_token.json` | DrChrono OAuth tokens (auto-created) |

## Troubleshooting

**"No DrChrono token found"** — Run `python auth_drchrono.py` first.

**"Sync token expired"** — Normal. The script automatically falls back to a full sync.

**Google auth fails** — Delete `token.json` and re-run `sync.py` to re-authorize.

**DrChrono 401** — Token may have expired. The script auto-refreshes, but if it persists, re-run `auth_drchrono.py`.
