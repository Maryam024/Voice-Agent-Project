"""Google Calendar sync. Uses a service account and a TEST calendar shared with it.
If no key is set, events stay local (id starts with "local-") so the demo still works.

The key can come from:
- GOOGLE_SERVICE_ACCOUNT_JSON  (hosting: the key file as base64 or raw JSON, one env variable)
- GOOGLE_SERVICE_ACCOUNT_FILE  (your PC: path to the key file)"""
import base64
import json
import os

from .availability import TZ

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _credentials():
    from google.oauth2 import service_account

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        if not raw.startswith("{"):  # base64 is safer to paste than raw JSON
            raw = base64.b64decode(raw).decode()
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)

    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if path and os.path.exists(path):
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    return None


def _service():
    creds = _credentials()
    if not creds:
        return None
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _times(appt):
    return {
        "start": {"dateTime": appt.start.astimezone(TZ).isoformat(), "timeZone": str(TZ)},
        "end": {"dateTime": appt.end.astimezone(TZ).isoformat(), "timeZone": str(TZ)},
    }


def create_event(appt):
    service = _service()
    if not service or not appt.doctor.calendar_id:
        return f"local-{appt.id}"
    body = {"summary": f"{appt.patient_name} - {appt.purpose}", **_times(appt)}
    event = service.events().insert(calendarId=appt.doctor.calendar_id, body=body).execute()
    return event["id"]


def move_event(appt):
    if appt.calendar_event_id.startswith("local-") or not appt.calendar_event_id:
        return
    service = _service()
    if service:
        service.events().patch(
            calendarId=appt.doctor.calendar_id, eventId=appt.calendar_event_id, body=_times(appt)
        ).execute()


def delete_event(appt):
    if not appt.calendar_event_id or appt.calendar_event_id.startswith("local-"):
        return
    service = _service()
    if service:
        service.events().delete(calendarId=appt.doctor.calendar_id, eventId=appt.calendar_event_id).execute()