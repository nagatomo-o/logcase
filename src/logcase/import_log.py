"""Apache access.log 取り込み。解析は Python、書き込みは DuckDB の read_csv。"""
from __future__ import annotations

import csv
import gzip
import io
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from logcase.errors import AppError
from logcase.logformat import CompiledFormat, compile_logformat, parse_line
from logcase.messages import MSG

_NULL = "\\N"
CHUNK_LINES = 20_000
_ROWS_COLUMNS = {
    "line_no": "INTEGER",
    "raw_line": "VARCHAR",
    "time_display": "VARCHAR",
    "time_abs": "VARCHAR",
    "time_offset": "VARCHAR",
}
_VALUES_COLUMNS = {
    "line_no": "INTEGER",
    "identifier": "VARCHAR",
    "is_unset": "INTEGER",
    "str_value": "VARCHAR",
    "num_value": "BIGINT",
}


@dataclass
class ImportResult:
    read_count: int
    inserted_count: int
    discarded_count: int
    duplicate_count: int


@dataclass
class ImportContext:
    compiled: CompiledFormat
    known_ids: set[str]


@dataclass
class ParsedImport:
    read_count: int = 0
    discarded_count: int = 0
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    values: list[tuple[Any, ...]] = field(default_factory=list)


def _strip_nl(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1]
    return line


def load_import_context(conn: Any) -> ImportContext:
    logformat = conn.execute("SELECT logformat FROM meta").fetchone()[0]
    field_rows = conn.execute("SELECT identifier, specifier FROM log_fields").fetchall()
    include_derived = any(row[1] == "derived:%r" for row in field_rows)
    known_ids = {row[0] for row in field_rows}
    compiled = compile_logformat(logformat, include_derived=include_derived)
    return ImportContext(compiled=compiled, known_ids=known_ids)


def parse_import_lines(ctx: ImportContext, lines: Iterable[str]) -> ParsedImport:
    parsed = ParsedImport()
    line_no = 0
    for raw in lines:
        line_no += 1
        parsed.read_count += 1
        content = _strip_nl(raw)
        if content == "":
            parsed.discarded_count += 1
            continue
        row = parse_line(ctx.compiled, content)
        if row is None:
            parsed.discarded_count += 1
            continue
        time_abs = row.time_abs.isoformat() if row.time_abs is not None else None
        parsed.rows.append(
            (line_no, content, row.time_display, time_abs, row.time_offset)
        )
        types = {f.identifier: f.field_type for f in ctx.compiled.fields}
        for value in row.values:
            if value.identifier not in ctx.known_ids:
                continue
            if types.get(value.identifier) == "datetime":
                continue
            parsed.values.append(
                (
                    line_no,
                    value.identifier,
                    int(value.is_unset),
                    value.str_value,
                    value.num_value,
                )
            )
    return parsed


def parse_import_bytes(filename: str, data: bytes, ctx: ImportContext) -> ParsedImport:
    try:
        if filename.lower().endswith(".gz"):
            with gzip.open(
                io.BytesIO(data), "rt", encoding="utf-8", errors="replace"
            ) as handle:
                return parse_import_lines(ctx, handle)
        text = data.decode("utf-8", errors="replace")
        return parse_import_lines(ctx, io.StringIO(text))
    except AppError:
        raise
    except Exception as exc:
        raise AppError("E_WRK_SYS01", MSG["E_WRK_SYS01"]) from exc


def _rollback_quietly(conn: Any) -> None:
    try:
        conn.execute("ROLLBACK")
    except Exception:
        pass


def _discard_rows_after(conn: Any, filename: str, start_row_id: int) -> None:
    """この取り込みで足した行だけ消す。同じファイル名の既存行は残す。"""
    _rollback_quietly(conn)
    try:
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM log_values WHERE row_id IN "
            "(SELECT row_id FROM log_rows WHERE source_filename = ? AND row_id > ?)",
            (filename, start_row_id),
        )
        conn.execute(
            "DELETE FROM custom_values WHERE row_id IN "
            "(SELECT row_id FROM log_rows WHERE source_filename = ? AND row_id > ?)",
            (filename, start_row_id),
        )
        conn.execute(
            "DELETE FROM log_rows WHERE source_filename = ? AND row_id > ?",
            (filename, start_row_id),
        )
        conn.execute("COMMIT")
    except Exception:
        _rollback_quietly(conn)
        raise


def _record_import(
    conn: Any,
    filename: str,
    read_count: int,
    inserted: int,
    discarded: int,
    duplicate: int,
) -> None:
    conn.execute(
        """
        INSERT INTO import_files
          (id, source_filename, imported_at, read_count, inserted_count,
           discarded_count, duplicate_count)
        SELECT
          COALESCE((SELECT MAX(id) FROM import_files), 0) + 1,
          ?, ?, ?, ?, ?, ?
        """,
        (
            filename,
            datetime.now(timezone.utc).isoformat(),
            read_count,
            inserted,
            discarded,
            duplicate,
        ),
    )


def _insert_chunk(
    conn: Any,
    filename: str,
    rows: list[tuple[Any, ...]],
    values: list[tuple[Any, ...]],
) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="logcase-import-"))
    inserted = 0
    try:
        rows_csv = tmp / "rows.csv"
        values_csv = tmp / "values.csv"
        _write_csv(rows_csv, list(_ROWS_COLUMNS), rows)
        _write_csv(values_csv, list(_VALUES_COLUMNS), values)
        conn.execute("DROP TABLE IF EXISTS incoming_rows")
        conn.execute("DROP TABLE IF EXISTS incoming_values")
        conn.execute("DROP TABLE IF EXISTS accepted_rows")
        conn.execute(
            f"CREATE TEMP TABLE incoming_rows AS {_read_csv_sql(rows_csv, _ROWS_COLUMNS)}"
        )
        conn.execute(
            f"CREATE TEMP TABLE incoming_values AS {_read_csv_sql(values_csv, _VALUES_COLUMNS)}"
        )
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TEMP TABLE accepted_rows AS
            SELECT
              COALESCE((SELECT MAX(row_id) FROM log_rows), 0)
                + ROW_NUMBER() OVER (ORDER BY line_no) AS row_id,
              line_no, raw_line, time_display, time_abs, time_offset
            FROM incoming_rows
            WHERE NOT EXISTS (
              SELECT 1 FROM log_rows existing WHERE existing.raw_line = incoming_rows.raw_line
            )
            QUALIFY ROW_NUMBER() OVER (PARTITION BY raw_line ORDER BY line_no) = 1
            """
        )
        conn.execute(
            """
            INSERT INTO log_rows
              (row_id, source_filename, line_no, raw_line, time_display, time_abs, time_offset)
            SELECT row_id, ?, line_no, raw_line, time_display, time_abs, time_offset
            FROM accepted_rows
            """,
            (filename,),
        )
        conn.execute(
            """
            INSERT INTO log_values (row_id, identifier, is_unset, str_value, num_value)
            SELECT a.row_id, v.identifier, v.is_unset, v.str_value, v.num_value
            FROM incoming_values v
            JOIN accepted_rows a ON a.line_no = v.line_no
            """
        )
        inserted = conn.execute("SELECT COUNT(*) FROM accepted_rows").fetchone()[0]
        conn.execute("COMMIT")
    except AppError:
        _rollback_quietly(conn)
        raise
    except Exception as exc:
        _rollback_quietly(conn)
        raise AppError("E_WRK_SYS01", MSG["E_WRK_SYS01"]) from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for table in ("incoming_rows", "incoming_values", "accepted_rows"):
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            except Exception:
                pass
    return int(inserted)


def _append_parsed_line(
    ctx: ImportContext,
    types: dict[str, str],
    line_no: int,
    raw: str,
    rows: list[tuple[Any, ...]],
    values: list[tuple[Any, ...]],
) -> bool:
    """解釈できた行を蓄積する。破棄したときは True。"""
    content = _strip_nl(raw)
    if content == "":
        return True
    row = parse_line(ctx.compiled, content)
    if row is None:
        return True
    time_abs = row.time_abs.isoformat() if row.time_abs is not None else None
    rows.append((line_no, content, row.time_display, time_abs, row.time_offset))
    for value in row.values:
        if value.identifier not in ctx.known_ids:
            continue
        if types.get(value.identifier) == "datetime":
            continue
        values.append(
            (
                line_no,
                value.identifier,
                int(value.is_unset),
                value.str_value,
                value.num_value,
            )
        )
    return False


def _import_iter(
    conn: Any, filename: str, lines: Iterable[str], ctx: ImportContext
) -> ImportResult:
    types = {f.identifier: f.field_type for f in ctx.compiled.fields}
    read_count = 0
    discarded = 0
    inserted = 0
    rows: list[tuple[Any, ...]] = []
    values: list[tuple[Any, ...]] = []
    line_no = 0
    start_row_id = int(
        conn.execute("SELECT COALESCE(MAX(row_id), 0) FROM log_rows").fetchone()[0]
    )
    try:
        for raw in lines:
            line_no += 1
            read_count += 1
            if _append_parsed_line(ctx, types, line_no, raw, rows, values):
                discarded += 1
            if len(rows) >= CHUNK_LINES:
                inserted += _insert_chunk(conn, filename, rows, values)
                rows.clear()
                values.clear()
        if rows:
            inserted += _insert_chunk(conn, filename, rows, values)
            rows.clear()
            values.clear()
        duplicate = read_count - discarded - inserted
        _record_import(conn, filename, read_count, inserted, discarded, duplicate)
    except Exception:
        _discard_rows_after(conn, filename, start_row_id)
        raise
    return ImportResult(
        read_count=read_count,
        inserted_count=inserted,
        discarded_count=discarded,
        duplicate_count=duplicate,
    )


def write_parsed_import(conn: Any, filename: str, parsed: ParsedImport) -> ImportResult:
    start_row_id = int(
        conn.execute("SELECT COALESCE(MAX(row_id), 0) FROM log_rows").fetchone()[0]
    )
    try:
        inserted = 0
        if parsed.rows:
            inserted = _insert_chunk(conn, filename, parsed.rows, parsed.values)
        duplicate = parsed.read_count - parsed.discarded_count - inserted
        _record_import(
            conn,
            filename,
            parsed.read_count,
            inserted,
            parsed.discarded_count,
            duplicate,
        )
    except Exception:
        _discard_rows_after(conn, filename, start_row_id)
        raise
    return ImportResult(
        read_count=parsed.read_count,
        inserted_count=inserted,
        discarded_count=parsed.discarded_count,
        duplicate_count=duplicate,
    )


def import_file(conn: Any, filename: str, lines: Iterable[str]) -> ImportResult:
    return _import_iter(conn, filename, lines, load_import_context(conn))


def _iter_staged_lines(path: Path, filename: str) -> Iterable[str]:
    if filename.lower().endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            yield from handle
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        yield from handle


def import_staged_file(conn: Any, filename: str, path: Path) -> ImportResult:
    return _import_iter(conn, filename, _iter_staged_lines(path, filename), load_import_context(conn))


def import_gzip_bytes(conn: Any, filename: str, data: bytes) -> ImportResult:
    if filename.lower().endswith(".gz"):
        handle: Any = gzip.open(io.BytesIO(data), "rt", encoding="utf-8", errors="replace")
    else:
        handle = io.StringIO(data.decode("utf-8", errors="replace"))
    with handle:
        return import_file(conn, filename, handle)


def delete_imported_file(conn: Any, filename: str) -> int:
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
        deleted = conn.execute(
            "SELECT COUNT(*) FROM log_rows WHERE source_filename = ?",
            (filename,),
        ).fetchone()[0]
        conn.execute("DELETE FROM log_rows WHERE source_filename = ?", (filename,))
        conn.execute("DELETE FROM import_files WHERE source_filename = ?", (filename,))
        conn.execute("COMMIT")
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise AppError("E_WRK_SYS01", MSG["E_WRK_SYS01"]) from exc
    return int(deleted)


def import_worker_count(file_count: int) -> int:
    env = os.environ.get("LOGCASE_IMPORT_WORKERS", "").strip()
    cap = int(env) if env.isdigit() and int(env) > 0 else 4
    return max(1, min(cap, file_count, os.cpu_count() or 1))


def _write_csv(path: Path, header: list[str], rows: list[tuple[Any, ...]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([_NULL if value is None else value for value in row])


def _read_csv_sql(path: Path, columns: dict[str, str]) -> str:
    posix = path.resolve().as_posix().replace("'", "''")
    mapping = ", ".join(f"'{name}': '{typ}'" for name, typ in columns.items())
    return (
        f"SELECT * FROM read_csv('{posix}', "
        "header=true, delim=',', quote='\"', escape='\"', "
        f"nullstr='\\N', auto_detect=false, columns={{{mapping}}})"
    )
