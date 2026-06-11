"""Deploy the generated MuleSoft app to Anypoint dev (spec §10 deploy, M5).

Uses the Anypoint Platform API with connected-app (client credentials)
auth. NOTE (spec §10): the institute may instead require anypoint-cli —
confirm during M5 and swap the transport here if so; the public function
signature stays the same.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential

ANYPOINT_BASE = "https://anypoint.mulesoft.com"


def app_name_for_job(job_name: str) -> str:
    """D11 namespacing: cdu-<job-name-with-dashes>."""
    return f"cdu-{job_name.replace('_', '-')}"


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, min=2, max=20), reraise=True)
def deploy_app(anypoint: dict, flow_xml_path: Path, job_name: str) -> str:
    """Package and deploy the generated flow; return the deployed app name.

    `anypoint` is a resolved connection dict — contains live credentials;
    never log it.
    """
    token = _get_token(anypoint["client_id"], anypoint["client_secret"])
    app_name = app_name_for_job(job_name)
    _deploy(token, anypoint, app_name, flow_xml_path)
    return app_name


def _get_token(client_id: str, client_secret: str) -> str:
    payload = json.dumps({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{ANYPOINT_BASE}/accounts/api/v2/oauth2/token",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())["access_token"]


def _deploy(token: str, anypoint: dict, app_name: str, flow_xml_path: Path) -> None:
    # Packaging a single flow XML into a deployable Mule artifact and the
    # exact Runtime Manager API calls depend on the institute's Anypoint
    # setup (CloudHub vs hybrid). Finalized in M5 against the dev org.
    raise NotImplementedError(
        "MuleSoft deployment transport is finalized in milestone M5 against "
        "the institute's Anypoint dev org (API vs anypoint-cli) — see spec §10."
    )
