"""Slack integration (docs/04 §slack.py): confirm buttons, reports, signatures."""

import hashlib
import hmac
import time

from slack_sdk import WebClient

from pm_agent.config import Settings
from pm_agent.integrations.base import FetchResult
from pm_agent.models import DataFlag, Severity, Source

_STALE_SECONDS = 300  # reject signatures more than 5 minutes away from now
_BUTTON_TEXT_LIMIT = 75


def _option_letter(index: int) -> str:
    return chr(ord("A") + index)


def _button_text(proposed: bool, index: int, option: str) -> str:
    check = "✅ " if proposed else ""
    label = f"{check}Choose {_option_letter(index)} — {option}"
    if len(label) <= _BUTTON_TEXT_LIMIT:
        return label
    prefix = f"{check}Choose {_option_letter(index)} — "
    return prefix + option[: max(_BUTTON_TEXT_LIMIT - len(prefix) - 1, 0)] + "…"


def post_confirm(
    settings: Settings,
    bet_id: str,
    options: list[str],
    proposed_index: int,
    hypothesis_summary: str,
) -> FetchResult[None]:
    """Post the confirmation message with buttons to the PM.

    Block Kit contract exactly per docs/04 — the /slack/interactions route
    matches on block_id (``bet_confirm:{bet_id}``), action_ids
    (``confirm_option:{i}``) and value (``bet_id``). Failures -> CRITICAL
    ``slack_post_failed`` flag; never raises (ADR 0005).
    """
    try:
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "📝 *Bet confirmation needed*\n"
                        f"*Hypothesis:* {hypothesis_summary}\n"
                        "*I believe you chose:* "
                        f"{_option_letter(proposed_index)} — {options[proposed_index]}"
                    ),
                },
            },
            {
                "type": "actions",
                "block_id": f"bet_confirm:{bet_id}",
                "elements": [
                    {
                        "type": "button",
                        "action_id": f"confirm_option:{i}",
                        "text": {
                            "type": "plain_text",
                            "text": _button_text(i == proposed_index, i, option),
                        },
                        "value": bet_id,
                        **({"style": "primary"} if i == proposed_index else {}),
                    }
                    for i, option in enumerate(options)
                ],
            },
        ]
        client = WebClient(token=settings.slack_bot_token)
        client.chat_postMessage(
            channel=settings.slack_channel_id,
            blocks=blocks,
            text="Bet confirmation needed — please choose an option.",
        )
    except Exception as e:
        return FetchResult(
            None, DataFlag(Source.SLACK, Severity.CRITICAL, f"slack_post_failed: {e}")
        )
    return FetchResult(None, None)


def post_report(settings: Settings, text: str) -> FetchResult[None]:
    """Post a plain-text report to the channel. Never raises (ADR 0005)."""
    try:
        client = WebClient(token=settings.slack_bot_token)
        client.chat_postMessage(channel=settings.slack_channel_id, text=text)
    except Exception as e:
        return FetchResult(
            None, DataFlag(Source.SLACK, Severity.CRITICAL, f"slack_post_failed: {e}")
        )
    return FetchResult(None, None)


def verify_slack_signature(settings: Settings, body: bytes, timestamp: str, signature: str) -> bool:
    """HMAC-SHA256 of ``v0:{ts}:{body}`` with the signing secret.

    Constant-time compare (``hmac.compare_digest``); rejects malformed and
    stale timestamps more than 5 minutes away from now (future included).
    """
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > _STALE_SECONDS:
        return False
    base = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(
        settings.slack_signing_secret.encode("utf-8"), base, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature)
