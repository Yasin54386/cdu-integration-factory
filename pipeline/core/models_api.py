"""GitHub Models REST API wrapper (inference.ai.azure.com).

Used by draft-intent (Task 2) and generate (Task 4) in place of the
copilot CLI. Any GitHub token with models:read scope works — the workflow
supplies GH_PIPELINE_TOKEN; locally a personal PAT with that scope is fine.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

MODELS_API_URL = "https://models.inference.ai.azure.com/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

# Env vars checked in order when no token is provided explicitly.
_TOKEN_ENV_VARS = [
    "GH_PIPELINE_TOKEN",
    "GITHUB_TOKEN",
    "COPILOT_TOKEN",
    "GH_TOKEN",
]


class ModelsAPIError(RuntimeError):
    pass


def find_token() -> str | None:
    """Return the first GitHub token found in the environment, or None."""
    for var in _TOKEN_ENV_VARS:
        val = os.environ.get(var)
        if val:
            return val
    return None


def call(
    *,
    user_prompt: str,
    system_prompt: str,
    token: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """Call the GitHub Models API and return the assistant message text.

    Raises ModelsAPIError on HTTP errors or empty responses.
    Does NOT retry — callers that need retry wrap this with tenacity.
    """
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    request = urllib.request.Request(
        MODELS_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise ModelsAPIError(
            f"GitHub Models API returned HTTP {exc.code}: {body}"
        ) from exc

    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:
        raise ModelsAPIError(
            f"Unexpected response shape from GitHub Models API: {data}"
        ) from exc

    if not text:
        raise ModelsAPIError("GitHub Models API returned empty content")
    return text
