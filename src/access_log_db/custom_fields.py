from __future__ import annotations

import sqlite3

from access_log_db.errors import AppError
from access_log_db.filters import Condition, _where
from access_log_db.messages import MSG
from access_log_db.names import validate_name


def add_custom_field(conn: sqlite3.Connection, name: str) -> None:
    name = validate_name(name, "E_WRK_REQ03", "E_WRK_FMT04")
    log_ids = {
        r[0] for r in conn.execute("SELECT identifier FROM log_fields")
    }
    log_ids.update({"row_id", "source_filename", "line_no"})
    if name in log_ids:
        raise AppError("E_WRK_DUP02", MSG["E_WRK_DUP02"])
    exists = conn.execute(
        "SELECT 1 FROM custom_fields WHERE name = ?", (name,)
    ).fetchone()
    if exists:
        raise AppError("E_WRK_DUP01", MSG["E_WRK_DUP01"])
    order = conn.execute(
        "SELECT COALESCE(MAX(display_order), -1) + 1 FROM custom_fields"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO custom_fields (name, display_order) VALUES (?, ?)",
        (name, order),
    )
    conn.commit()


def delete_custom_field(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM custom_values WHERE name = ?", (name,))
    conn.execute("DELETE FROM custom_fields WHERE name = ?", (name,))
    conn.commit()


def set_custom_value(
    conn: sqlite3.Connection, row_id: int, name: str, value: str
) -> None:
    is_unset = 1 if value == "" else 0
    str_value = None if is_unset else value
    conn.execute(
        """INSERT INTO custom_values (row_id, name, is_unset, str_value)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(row_id, name) DO UPDATE SET
             is_unset=excluded.is_unset, str_value=excluded.str_value""",
        (row_id, name, is_unset, str_value),
    )
    conn.commit()


def bulk_set_custom(
    conn: sqlite3.Connection,
    name: str,
    value: str,
    conditions: list[Condition],
) -> int:
    if not name:
        raise AppError("E_WRK_REQ02", MSG["E_WRK_REQ02"])
    exists = conn.execute(
        "SELECT 1 FROM custom_fields WHERE name = ?", (name,)
    ).fetchone()
    if not exists:
        raise AppError("E_WRK_REQ02", MSG["E_WRK_REQ02"])
    where, params = _where(conditions)
    ids = [
        r[0]
        for r in conn.execute(
            f"SELECT r.row_id FROM log_rows r WHERE {where}", params
        )
    ]
    is_unset = 1 if value == "" else 0
    str_value = None if is_unset else value
    for row_id in ids:
        conn.execute(
            """INSERT INTO custom_values (row_id, name, is_unset, str_value)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(row_id, name) DO UPDATE SET
                 is_unset=excluded.is_unset, str_value=excluded.str_value""",
            (row_id, name, is_unset, str_value),
        )
    conn.commit()
    return len(ids)
