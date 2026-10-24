from __future__ import annotations

import gzip
import io
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from logcase.errors import AppError
from logcase.logformat import compile_logformat, parse_line
from logcase.messages import MSG


@dataclass
class ImportResult:
    read_count: int
    inserted_count: int
    discarded_count: int
    duplicate_count: int


def _strip_nl(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1]
    return line


def import_file(
    conn: sqlite3.Connection, filename: str, lines: Iterable[str]
) -> ImportResult:
    logformat = conn.execute("SELECT logformat FROM meta").fetchone()[0]
    field_rows = conn.execute(
        "SELECT identifier, specifier FROM log_fields"
    ).fetchall()
    include_derived = any(row[1] == "derived:%r" for row in field_rows)
    known_ids = {row[0] for row in field_rows}
    compiled = compile_logformat(logformat, include_derived=include_derived)
    result = ImportResult(0, 0, 0, 0)
    try:
        conn.execute("BEGIN")
        next_id = conn.execute(
            "SELECT COALESCE(MAX(row_id), 0) FROM log_rows"
        ).fetchone()[0] + 1
        seen: set[str] = set()
        line_no = 0
        for raw in lines:
            line_no += 1
            result.read_count += 1
            content = _strip_nl(raw)
            if content == "":
                result.discarded_count += 1
                continue
            exists = conn.execute(
                "SELECT 1 FROM log_rows WHERE raw_line = ?", (content,)
            ).fetchone()
            if exists or content in seen:
                result.duplicate_count += 1
                continue
            parsed = parse_line(compiled, content)
            if parsed is None:
                result.discarded_count += 1
                continue
            time_abs = None
            if parsed.time_abs is not None:
                time_abs = parsed.time_abs.isoformat()
            conn.execute(
                """INSERT INTO log_rows
                   (row_id, source_filename, line_no, raw_line,
                    time_display, time_abs, time_offset)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    next_id,
                    filename,
                    line_no,
                    content,
                    parsed.time_display,
                    time_abs,
                    parsed.time_offset,
                ),
            )
            for value in parsed.values:
                if value.identifier not in known_ids:
                    continue
                field = next(f for f in compiled.fields if f.identifier == value.identifier)
                if field.field_type == "datetime":
                    continue
                conn.execute(
                    """INSERT INTO log_values
                       (row_id, identifier, is_unset, str_value, num_value)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        next_id,
                        value.identifier,
                        int(value.is_unset),
                        value.str_value,
                        value.num_value,
                    ),
                )
            seen.add(content)
            next_id += 1
            result.inserted_count += 1
        conn.execute(
            """INSERT INTO import_files
               (source_filename, imported_at, read_count, inserted_count,
                discarded_count, duplicate_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                filename,
                datetime.now(timezone.utc).isoformat(),
                result.read_count,
                result.inserted_count,
                result.discarded_count,
                result.duplicate_count,
            ),
        )
        conn.commit()
    except AppError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise AppError("E_WRK_SYS01", MSG["E_WRK_SYS01"]) from exc
    return result


def delete_imported_file(conn: sqlite3.Connection, filename: str) -> int:
    if not filename:
        return 0
    exists = conn.execute(
        "SELECT 1 FROM import_files WHERE source_filename = ? "
        "UNION ALL SELECT 1 FROM log_rows WHERE source_filename = ? LIMIT 1",
        (filename, filename),
    ).fetchone()
    if not exists:
        return 0
    try:
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM log_values WHERE row_id IN "
            "(SELECT row_id FROM log_rows WHERE source_filename = ?)",
            (filename,),
        )
        conn.execute(
            "DELETE FROM custom_values WHERE row_id IN "
            "(SELECT row_id FROM log_rows WHERE source_filename = ?)",
            (filename,),
        )
        cur = conn.execute(
            "DELETE FROM log_rows WHERE source_filename = ?",
            (filename,),
        )
        conn.execute(
            "DELETE FROM import_files WHERE source_filename = ?",
            (filename,),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise AppError("E_WRK_SYS01", MSG["E_WRK_SYS01"]) from exc
    return cur.rowcount


def import_gzip_bytes(
    conn: sqlite3.Connection, filename: str, data: bytes
) -> ImportResult:
    with gzip.open(io.BytesIO(data), "rt", encoding="utf-8", errors="replace") as handle:
        return import_file(conn, filename, handle)
