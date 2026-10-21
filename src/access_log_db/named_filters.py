from __future__ import annotations

import json
import sqlite3

from access_log_db.errors import AppError
from access_log_db.filters import Condition
from access_log_db.messages import MSG
from access_log_db.names import validate_name


def save_named_filter(
    conn: sqlite3.Connection, name: str, spec: list[dict], overwrite: bool
) -> None:
    name = validate_name(name, "E_WRK_REQ04", "E_WRK_FMT04")
    exists = conn.execute(
        "SELECT 1 FROM named_filters WHERE name = ?", (name,)
    ).fetchone()
    payload = json.dumps(spec, ensure_ascii=False)
    if exists and not overwrite:
        raise AppError("W_WRK_BIZ03", MSG["W_WRK_BIZ03"])
    if exists:
        conn.execute(
            "UPDATE named_filters SET spec = ? WHERE name = ?", (payload, name)
        )
    else:
        conn.execute(
            "INSERT INTO named_filters (name, spec) VALUES (?, ?)", (name, payload)
        )
    conn.commit()


def load_named_filter(conn: sqlite3.Connection, name: str) -> list[dict]:
    row = conn.execute(
        "SELECT spec FROM named_filters WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise AppError("E_WRK_EXS01", MSG["E_WRK_EXS01"])
    return json.loads(row[0])


def list_named_filters(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM named_filters ORDER BY name")]


def delete_named_filter(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM named_filters WHERE name = ?", (name,))
    conn.commit()


def conditions_from_spec(spec: list[dict]) -> list[Condition]:
    return [
        Condition(
            identifier=item["identifier"],
            source=item["source"],
            field_type=item["field_type"],
            op=item["op"],
            value=item.get("value"),
        )
        for item in spec
    ]
