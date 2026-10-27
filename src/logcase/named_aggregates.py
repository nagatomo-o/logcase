from __future__ import annotations

import json
from typing import Any

from logcase.errors import AppError
from logcase.messages import MSG
from logcase.names import validate_name


def ensure_named_aggregates(conn: Any) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS named_aggregates ("
        "name TEXT PRIMARY KEY, spec TEXT NOT NULL)"
    )


def save_named_aggregate(
    conn: Any, name: str, spec: dict, overwrite: bool
) -> None:
    ensure_named_aggregates(conn)
    name = validate_name(name, "E_WRK_REQ05", "E_WRK_FMT04")
    exists = conn.execute(
        "SELECT 1 FROM named_aggregates WHERE name = ?", (name,)
    ).fetchone()
    payload = json.dumps(spec, ensure_ascii=False)
    if exists and not overwrite:
        raise AppError("W_WRK_BIZ07", MSG["W_WRK_BIZ07"])
    if exists:
        conn.execute(
            "UPDATE named_aggregates SET spec = ? WHERE name = ?", (payload, name)
        )
    else:
        conn.execute(
            "INSERT INTO named_aggregates (name, spec) VALUES (?, ?)",
            (name, payload),
        )
    conn.commit()


def load_named_aggregate(conn: Any, name: str) -> dict:
    ensure_named_aggregates(conn)
    row = conn.execute(
        "SELECT spec FROM named_aggregates WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise AppError("E_WRK_EXS02", MSG["E_WRK_EXS02"])
    data = json.loads(row[0])
    if not isinstance(data, dict):
        raise AppError("E_WRK_EXS02", MSG["E_WRK_EXS02"])
    return data


def list_named_aggregates(conn: Any) -> list[str]:
    ensure_named_aggregates(conn)
    return [
        r[0]
        for r in conn.execute("SELECT name FROM named_aggregates ORDER BY name")
    ]


def delete_named_aggregate(conn: Any, name: str) -> None:
    ensure_named_aggregates(conn)
    conn.execute("DELETE FROM named_aggregates WHERE name = ?", (name,))
    conn.commit()
