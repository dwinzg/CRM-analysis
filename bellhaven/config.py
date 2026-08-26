"""Constants and configuration. No logic lives here on purpose — this is the file
you edit during a live demo to retune the matcher."""
import os

from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://analyst-assessment-production.up.railway.app"
API_BASE = f"{BASE_URL}/api/v1"

#: The parent account every current Bellhaven community should hang off.
BELLHAVEN_PARENT_ID = "0015QAPLGS3FVYEEEM"

#: The website and the CRM use different care vocabularies. This maps website
#: language onto CRM language. The mapping is lossy — a site may list two
#: offerings where the CRM field holds one — so it is only ever used to FILL a
#: blank care_type, never to overwrite a populated one.
CARE_MAP = {
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
    "Memory Support": "Memory Care",
    "Assisted Living": "Assisted Living",
    "Independent Living": "Independent Living",
}

#: Name-similarity score (0-100) at or above which a name match is trusted
#: outright, provided the city+state gate has already passed.
NAME_CONFIDENT = 88
#: ...and the floor below which a name match is not worth a reviewer's time.
NAME_REVIEW = 70

DB_PATH = os.environ.get("BELLHAVEN_DB", "data/ledger.db")

VALID_STATUSES = ("Active", "Inactive", "Needs Review")


def token() -> str:
    t = os.environ.get("CRM_TOKEN", "")
    if not t:
        raise RuntimeError("CRM_TOKEN missing. Copy .env.example to .env and fill it in.")
    return t
