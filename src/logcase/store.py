from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from logcase.duckdb_store import (
    DuckConn,
    convert_sqlite_to_duckdb,
    open_duckdb_file,
)
from logcase.errors import AppError
from logcase.logformat import compile_logformat
from logcase.messages import MSG
from logcase.names import validate_name

SCHEMA = """
CREATE TABLE meta (
  name TEXT NOT NULL,
  logformat TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE log_fields (
  identifier TEXT PRIMARY KEY,
  field_type TEXT NOT NULL,
  specifier TEXT NOT NULL,
  display_order INTEGER NOT NULL,
  time_axis INTEGER NOT NULL,
  key TEXT NOT NULL
);
CREATE TABLE custom_fields (
  name TEXT PRIMARY KEY,
  display_order INTEGER NOT NULL
);
CREATE TABLE import_files (
  id INTEGER PRIMARY KEY,
  source_filename TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  read_count INTEGER NOT NULL,
  inserted_count INTEGER NOT NULL,
  discarded_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL
);
CREATE TABLE log_rows (
  row_id INTEGER PRIMARY KEY,
  source_filename TEXT NOT NULL,
  line_no INTEGER NOT NULL,
  raw_line TEXT NOT NULL UNIQUE,
  time_display TEXT,
  time_abs TEXT,
  time_offset TEXT
);
CREATE TABLE log_values (
  row_id INTEGER NOT NULL,
  identifier TEXT NOT NULL,
  is_unset INTEGER NOT NULL,
  str_value TEXT,
  num_value INTEGER,
  PRIMARY KEY (row_id, identifier)
);
CREATE TABLE custom_values (
  row_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  is_unset INTEGER NOT NULL,
  str_value TEXT,
  PRIMARY KEY (row_id, name)
);
CREATE TABLE named_filters (
  name TEXT PRIMARY KEY,
  spec TEXT NOT NULL
);
CREATE INDEX idx_log_values_str ON log_values(identifier, str_value);
CREATE INDEX idx_log_values_num ON log_values(identifier, num_value);
CREATE INDEX idx_log_rows_time_abs ON log_rows(time_abs);
CREATE INDEX idx_log_rows_raw ON log_rows(raw_line);
CREATE INDEX idx_log_rows_source ON log_rows(source_filename);
CREATE INDEX idx_custom_values_str ON custom_values(name, str_value);
"""


def case_path(data_dir: Path, case_id: str) -> Path:
    return data_dir / f"{case_id}.duckdb"


def sqlite_case_path(data_dir: Path, case_id: str) -> Path:
    return data_dir / f"{case_id}.sqlite"


def _public_case_id(case_id: str) -> UUID | None:
    try:
        parsed = UUID(case_id)
    except (ValueError, TypeError, AttributeError):
        return None
    if parsed.version != 4 or str(parsed) != case_id:
        return None
    return parsed


def case_exists(data_dir: Path, case_id: str) -> bool:
    if _public_case_id(case_id) is None:
        return False
    return case_path(data_dir, case_id).is_file() or sqlite_case_path(data_dir, case_id).is_file()


def init_schema(conn: DuckConn) -> None:
    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def open_case(data_dir: Path, case_id: str) -> DuckConn:
    if _public_case_id(case_id) is None:
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"])
    path = case_path(data_dir, case_id)
    leftover = sqlite_case_path(data_dir, case_id)
    if not path.is_file() and leftover.is_file():
        convert_sqlite_to_duckdb(leftover, path)
    if not path.is_file():
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"])
    return open_duckdb_file(path)


def create_case(data_dir: Path, name: str, logformat: str) -> str:
    name = validate_name(name, "E_CSE_REQ01", "E_CSE_FMT01")
    compiled = compile_logformat(logformat)
    data_dir.mkdir(parents=True, exist_ok=True)
    case_id = str(uuid4())
    path = case_path(data_dir, case_id)
    conn = DuckConn(path)
    try:
        init_schema(conn)
        conn.execute(
            "INSERT INTO meta (name, logformat, created_at) VALUES (?, ?, ?)",
            (name, logformat.strip(), datetime.now(timezone.utc).isoformat()),
        )
        for field in compiled.fields:
            conn.execute(
                """INSERT INTO log_fields
                   (identifier, field_type, specifier, display_order, time_axis, key)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    field.identifier,
                    field.field_type,
                    field.specifier,
                    field.order,
                    int(field.time_axis),
                    field.key,
                ),
            )
        conn.commit()
    except Exception:
        conn.close()
        path.unlink(missing_ok=True)
        raise
    conn.close()
    return case_id


def list_cases(data_dir: Path, skip_ids: set[str] | None = None) -> list[dict]:
    if not data_dir.exists():
        return []
    rows: list[dict] = []
    seen: set[str] = set()
    paths = sorted(data_dir.glob("*.duckdb"))
    leftovers = [
        path
        for path in sorted(data_dir.glob("*.sqlite"))
        if not case_path(data_dir, path.stem).is_file()
    ]
    for path in [*paths, *leftovers]:
        if path.stem in seen:
            continue
        seen.add(path.stem)
        if _public_case_id(path.stem) is None:
            rows.append({"id": path.stem, "error": path.name})
            continue
        if skip_ids and path.stem in skip_ids:
            rows.append({"id": path.stem, "skipped": True, "error": None})
            continue
        conn = None
        try:
            if path.suffix == ".sqlite":
                convert_sqlite_to_duckdb(path, case_path(data_dir, path.stem))
                conn = open_duckdb_file(case_path(data_dir, path.stem))
            else:
                conn = open_duckdb_file(path)
            meta = conn.execute("SELECT name, logformat FROM meta").fetchone()
            if meta is None:
                rows.append({"id": path.stem, "error": path.name})
                continue
            file_count = conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0]
            row_count = conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0]
            last = conn.execute("SELECT MAX(imported_at) FROM import_files").fetchone()[0]
            head = (meta["logformat"] or "")[:80]
            rows.append(
                {
                    "id": path.stem,
                    "name": meta["name"],
                    "logformat": meta["logformat"],
                    "logformat_head": head,
                    "file_count": file_count,
                    "row_count": row_count,
                    "last_import": last,
                    "error": None,
                }
            )
        except Exception:
            rows.append({"id": path.stem, "error": path.name})
        finally:
            if conn is not None:
                conn.close()
    return rows


def rename_case(data_dir: Path, case_id: str, name: str) -> None:
    name = validate_name(name, "E_CSE_REQ01", "E_CSE_FMT01")
    conn = open_case(data_dir, case_id)
    conn.execute("UPDATE meta SET name = ?", (name,))
    conn.commit()
    conn.close()


def delete_case(data_dir: Path, case_id: str) -> None:
    if _public_case_id(case_id) is None:
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"])
    path = case_path(data_dir, case_id)
    leftover = sqlite_case_path(data_dir, case_id)
    if not path.is_file() and not leftover.is_file():
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"])
    path.unlink(missing_ok=True)
    leftover.unlink(missing_ok=True)
    path.with_name(path.name + ".tmp").unlink(missing_ok=True)
