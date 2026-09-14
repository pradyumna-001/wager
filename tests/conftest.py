"""Test bootstrap: placeholder env vars set BEFORE any pm_agent import.

This runs at conftest module import time, which precedes collection of test
modules, so no test ever needs a real .env.
"""

import os

_PLACEHOLDERS = {
    "DATABASE_URL": "postgresql://test:test@localhost:5432/pm_agent_test",
    "LLM_BASE_URL": "http://localhost:9999/v1",
    "LLM_API_KEY": "test-llm-key",
    "LLM_MODEL": "test-model",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "/nonexistent/sa.json",
    "GOOGLE_DOC_ID": "test-doc-id",
    "GOOGLE_CALENDAR_ID": "test-calendar",
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_SIGNING_SECRET": "test-signing-secret",
    "SLACK_CHANNEL_ID": "C000TEST",
    "PM_SLACK_USER_ID": "U000TEST",
    "GITHUB_TOKEN": "ghp_test",
    "GITHUB_REPO": "test-owner/test-repo",
    "POSTHOG_HOST": "http://localhost:9999",
    "POSTHOG_PROJECT_ID": "0",
    "POSTHOG_PERSONAL_API_KEY": "phx_test",
    "DEMO_MODE": "false",
}

for _key, _value in _PLACEHOLDERS.items():
    os.environ.setdefault(_key, _value)
