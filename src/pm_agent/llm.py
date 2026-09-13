"""Single LLM gateway (ADR 0004).

The only module allowed to call an LLM. Structured output only: JSON-mode chat
completion validated into a Pydantic schema, with one repair retry on schema
validation failure. Transport errors from the OpenAI SDK propagate (fail-visible);
only schema-validation failure is retried and wrapped into ``LLMValidationError``.
"""

from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from pm_agent.config import Settings
from pm_agent.errors import LLMValidationError

T = TypeVar("T", bound=BaseModel)

_MAX_ATTEMPTS = 2

_RETRY_INSTRUCTION = (
    "Your previous response failed schema validation:\n{error}\n"
    "Return corrected JSON matching the schema."
)


def call_llm(settings: Settings, system: str, user: str, schema: type[T]) -> T:
    """Call the OpenAI-compatible endpoint and validate the reply into ``schema``.

    Uses ``response_format={"type": "json_object"}`` and temperature 0.2. On a
    ``ValidationError`` (including invalid JSON or empty content), retries once
    with the validation error appended to the prompt. A second failure raises
    ``LLMValidationError``.
    """
    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_error: ValidationError | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if attempt > 1 and last_error is not None:
            messages = [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"{user}\n\n{_RETRY_INSTRUCTION.format(error=last_error)}",
                },
            ]
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            last_error = exc
    raise LLMValidationError(
        f"LLM output failed schema validation after {_MAX_ATTEMPTS} attempts: {last_error}"
    )
