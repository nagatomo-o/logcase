from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

from logcase.errors import AppError
from logcase.filters import Condition, count_rows, fetch_rows
from logcase.messages import MSG

ROW_FIXED = ["行ID", "元ファイル名", "ファイル内行番号"]


def _writer(buffer: io.StringIO) -> csv.writer:
    return csv.writer(buffer, lineterminator="\r\n")


def _headers(conn: sqlite3.Connection) -> list[str]:
    logs = [r[0] for r in conn.execute(
        "SELECT identifier FROM log_fields ORDER BY display_order"
    )]
    customs = [r[0] for r in conn.execute(
        "SELECT name FROM custom_fields ORDER BY display_order"
    )]
    return ROW_FIXED + logs + customs


def _row_values(row: dict, headers: list[str]) -> list[object]:
    values: list[object] = [
        row["row_id"],
        row["source_filename"],
        row["line_no"],
    ]
    for header in headers[len(ROW_FIXED) :]:
        val = row.get(header)
        values.append("" if val is None else val)
    return values


def write_rows_csv(
    conn: sqlite3.Connection, conditions: list[Condition], path: Path
) -> int:
    try:
        headers = _headers(conn)
        total = count_rows(conn, conditions)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(headers)
            after = 0
            written = 0
            while written < total:
                chunk = fetch_rows(conn, conditions, after, limit=5000)
                if not chunk:
                    break
                for row in chunk:
                    writer.writerow(_row_values(row, headers))
                written += len(chunk)
                after = chunk[-1]["row_id"]
        return total
    except AppError:
        path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise AppError("E_EXP_SYS01", MSG["E_EXP_SYS01"]) from exc


def write_aggregate_csv(rows: list[dict], path: Path) -> int:
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            if not rows:
                return 0
            headers = list(rows[0].keys())
            writer.writerow(headers)
            for row in rows:
                writer.writerow(["" if row.get(h) is None else row.get(h) for h in headers])
        return len(rows)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise AppError("E_EXP_SYS01", MSG["E_EXP_SYS01"]) from exc


def rows_csv_bytes(
    conn: sqlite3.Connection,
    conditions: list[Condition],
    match_count: int | None = None,
) -> bytes:
    headers = _headers(conn)
    buf = io.StringIO()
    writer = _writer(buf)
    writer.writerow(headers)
    total = count_rows(conn, conditions) if match_count is None else match_count
    after = 0
    written = 0
    while written < total:
        chunk = fetch_rows(conn, conditions, after, limit=5000)
        if not chunk:
            break
        for row in chunk:
            writer.writerow(_row_values(row, headers))
        written += len(chunk)
        after = chunk[-1]["row_id"]
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def aggregate_csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = _writer(buf)
    if rows:
        headers = list(rows[0].keys())
        writer.writerow(headers)
        for row in rows:
            writer.writerow(["" if row.get(h) is None else row.get(h) for h in headers])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
