"""Google Docs transcript fetch (docs/04 §gdocs.py)."""

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from pm_agent.config import Settings
from pm_agent.integrations.base import FetchResult
from pm_agent.models import DataFlag, Severity, Source


def _build_credentials(settings: Settings) -> Credentials:
    return Credentials.from_service_account_file(settings.google_service_account_file)


def fetch_transcript(settings: Settings, doc_id: str) -> FetchResult[str]:
    """Fetch the full plain-text body of the Google Doc.

    Concatenates ``paragraph.elements[].textRun.content`` (tables and non-text
    elements ignored). Any failure in the fetch path -> CRITICAL
    ``gdocs_fetch_failed`` flag; an empty body -> ``gdocs_empty_doc``. Never
    raises (ADR 0005).
    """
    try:
        creds = _build_credentials(settings)
        service = build("docs", "v1", credentials=creds)
        doc = service.documents().get(documentId=doc_id).execute()
        text = "".join(
            element.get("textRun", {}).get("content", "")
            for paragraph in doc.get("body", {}).get("content", [])
            for element in paragraph.get("paragraph", {}).get("elements", [])
        )
    except Exception as e:
        return FetchResult(
            None, DataFlag(Source.GDOCS, Severity.CRITICAL, f"gdocs_fetch_failed: {e}")
        )
    if not text.strip():
        return FetchResult(None, DataFlag(Source.GDOCS, Severity.CRITICAL, "gdocs_empty_doc"))
    return FetchResult(text, None)
