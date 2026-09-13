"""Google Calendar review-event integration (docs/04 §gcal.py)."""

from datetime import date, timedelta

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from pm_agent.config import Settings
from pm_agent.integrations.base import FetchResult
from pm_agent.models import DataFlag, Severity, Source


def _build_credentials(settings: Settings) -> Credentials:
    return Credentials.from_service_account_file(settings.google_service_account_file)


def create_review_event(
    settings: Settings, review_date: str, title: str, description: str
) -> FetchResult[str]:
    """Create an all-day event on review_date; result value is the event id.

    Same SA credentials as gdocs (docs/04). All-day end is exclusive, so end is
    review_date + 1 day. Failures -> CRITICAL ``calendar_write_failed`` flag
    (docs/03 §schedule_review); never raises (ADR 0005).
    """
    try:
        creds = _build_credentials(settings)
        service = build("calendar", "v3", credentials=creds)
        event = {
            "summary": title,
            "description": description,
            "start": {"date": review_date},
            "end": {"date": (date.fromisoformat(review_date) + timedelta(days=1)).isoformat()},
        }
        created = (
            service.events().insert(calendarId=settings.google_calendar_id, body=event).execute()
        )
    except Exception as e:
        return FetchResult(
            None, DataFlag(Source.GCAL, Severity.CRITICAL, f"calendar_write_failed: {e}")
        )
    return FetchResult(created["id"], None)
