import os
from dotenv import load_dotenv

load_dotenv()

# DrChrono OAuth2
DRCHRONO_CLIENT_ID = os.getenv("DRCHRONO_CLIENT_ID", "")
DRCHRONO_CLIENT_SECRET = os.getenv("DRCHRONO_CLIENT_SECRET", "")
DRCHRONO_REDIRECT_URI = os.getenv("DRCHRONO_REDIRECT_URI", "http://localhost:8080/callback")

# DrChrono scheduling
DRCHRONO_DOCTOR_ID = os.getenv("DRCHRONO_DOCTOR_ID", "")
_office_ids = os.getenv("DRCHRONO_OFFICE_IDS", os.getenv("DRCHRONO_OFFICE_ID", ""))
DRCHRONO_OFFICE_IDS = [o.strip() for o in _office_ids.split(",") if o.strip()]
DRCHRONO_EXAM_ROOM = os.getenv("DRCHRONO_EXAM_ROOM", "")
DRCHRONO_BLOCK_PATIENT_ID = os.getenv("DRCHRONO_BLOCK_PATIENT_ID", "")
DRCHRONO_BLOCK_PROFILE_ID = os.getenv("DRCHRONO_BLOCK_PROFILE_ID", "")

# DrChrono API base
DRCHRONO_API_BASE = "https://app.drchrono.com/api"
DRCHRONO_TOKEN_URL = "https://drchrono.com/o/token/"
DRCHRONO_AUTH_URL = "https://drchrono.com/o/authorize/"

# Google Calendar — comma-separated list of calendar IDs to sync
_cal_ids = os.getenv("GOOGLE_CALENDAR_IDS", "primary")
GOOGLE_CALENDAR_IDS = [c.strip() for c in _cal_ids.split(",") if c.strip()]
GOOGLE_CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
GOOGLE_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")

# All-day event handling — only sync all-day events whose summary contains
# one of these keywords (case-insensitive). Comma-separated.
_allday_kw = os.getenv("ALLDAY_KEYWORDS", "vacation,out of office,off,block,pto,travel")
ALLDAY_KEYWORDS = [k.strip().lower() for k in _allday_kw.split(",") if k.strip()]

# Business hours used when converting all-day events to timed blocks
ALLDAY_BLOCK_START = os.getenv("ALLDAY_BLOCK_START", "08:00")  # HH:MM
ALLDAY_BLOCK_END = os.getenv("ALLDAY_BLOCK_END", "18:00")      # HH:MM

# Sync
SYNC_WINDOW_MONTHS = int(os.getenv("SYNC_WINDOW_MONTHS", "6"))
SYNC_STATE_FILE = os.path.join(os.path.dirname(__file__), "sync_state.json")

# Marker prefix so we can identify our blocks in DrChrono
BLOCK_NOTE_PREFIX = "[GCal Sync]"

# Shift buffer settings
QGENDA_CALENDAR_ID = os.getenv("QGENDA_CALENDAR_ID", "hadfield.neil@gmail.com")
SHIFT_PREFIX = os.getenv("SHIFT_PREFIX", "SL")
BUFFER_DURATION_MINUTES = int(os.getenv("BUFFER_DURATION_MINUTES", "120"))
BUFFER_PRE_LABEL = os.getenv("BUFFER_PRE_LABEL", "Prepare for shift")
BUFFER_POST_LABEL = os.getenv("BUFFER_POST_LABEL", "Post shift")
BUFFER_STATE_FILE = os.path.join(os.path.dirname(__file__), os.getenv("BUFFER_STATE_FILE", "buffer_state.json"))
BUFFER_EVENT_TAG = os.getenv("BUFFER_EVENT_TAG", "shift-buffer-script")

# DrChrono → Google filtered sync
DRCHRONO_ICS_URL = os.getenv("DRCHRONO_ICS_URL", "")
DRCHRONO_BLOCK_PATIENT_NAME = os.getenv("DRCHRONO_BLOCK_PATIENT_NAME", "UNTI07E4E294")
DRCHRONO_PATIENT_CALENDAR_NAME = os.getenv("DRCHRONO_PATIENT_CALENDAR_NAME", "DrChrono - Patient Appointments")
DRCHRONO_OTHER_CALENDAR_NAME = os.getenv("DRCHRONO_OTHER_CALENDAR_NAME", "DrChrono - Office")
DRCHRONO_PATIENT_CALENDAR_ID = os.getenv("DRCHRONO_PATIENT_CALENDAR_ID", "")
DRCHRONO_OTHER_CALENDAR_ID = os.getenv("DRCHRONO_OTHER_CALENDAR_ID", "")
DRCHRONO_SYNC_STATE_FILE = os.path.join(os.path.dirname(__file__), "drchrono_gcal_state.json")
DRCHRONO_SYNC_TAG = "drchrono-to-gcal-sync"
