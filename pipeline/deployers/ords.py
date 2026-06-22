"""Deploy the generated ORDS module to Oracle dev (spec §10 deploy, M5).

Runs the generated module SQL via oracledb. The module is namespaced by
job_name (D11), so redeploying replaces only this job's objects.
"""

from __future__ import annotations

import re
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, min=2, max=20), reraise=True)
def deploy_module(oracle: dict, module_sql_path: Path, job_name: str) -> str:
    """Execute the generated ORDS module SQL; return the endpoint base path.

    `oracle` is a resolved connection dict (resolver.resolve) — contains
    live credentials; never log it.
    """
    import oracledb

    dsn = _build_dsn(oracle)
    sql = module_sql_path.read_text(encoding="utf-8")
    with oracledb.connect(user=oracle["user"], password=oracle["password"],
                          dsn=dsn) as connection:
        with connection.cursor() as cursor:
            for statement in split_statements(sql):
                cursor.execute(statement)
        connection.commit()
    return f"/ords/cdu/{job_name}/"


def _build_dsn(oracle: dict) -> str:
    """Build an Oracle DSN from connection metadata.

    Supports both identifiers Oracle uses:
      - service name (modern): EZConnect `host:port/service`
      - SID (legacy, e.g. Callista `SUGR`): needs the TNS form, built via
        oracledb.makedsn(host, port, sid=...).
    A connection declares exactly one of `service` or `sid`.
    """
    import oracledb

    host, port = oracle["host"], oracle["port"]
    if oracle.get("sid"):
        return oracledb.makedsn(host, port, sid=oracle["sid"])
    if oracle.get("service"):
        return f"{host}:{port}/{oracle['service']}"
    raise KeyError(
        "oracle connection must define either 'service' or 'sid' in connections.yaml"
    )


def split_statements(sql: str) -> list[str]:
    """Split a module script into executable statements.

    PL/SQL blocks (BEGIN/DECLARE ... END;) terminate with a line holding
    only `/`; plain SQL statements terminate with `;`.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_plsql = False
    for line in sql.splitlines():
        stripped = line.strip()
        if not in_plsql and re.match(r"^(DECLARE|BEGIN)\b", stripped, re.IGNORECASE):
            in_plsql = True
        if in_plsql and stripped == "/":
            statements.append("\n".join(buffer).strip())
            buffer, in_plsql = [], False
            continue
        buffer.append(line)
        if not in_plsql and stripped.endswith(";"):
            statement = "\n".join(buffer).strip().rstrip(";")
            if statement:
                statements.append(statement)
            buffer = []
    tail = "\n".join(buffer).strip().rstrip(";")
    if tail:
        statements.append(tail)
    return [s for s in statements if s and not s.startswith("--")]
