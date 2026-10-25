from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

TABLES = (
    "meta",
    "log_fields",
    "custom_fields",
    "import_files",
    "log_rows",
    "log_values",
    "custom_values",
    "named_filters",
)

CHUNK = 50_000


def engine_name() -> str:
    return os.environ.get("LOGCASE_ENGINE", "duckdb").strip().lower()


def use_duckdb() -> bool:
    return engine_name() != "sqlite"


def duckdb_path_for(sqlite_path: Path) -> Path:
    return sqlite_path.with_suffix(".duckdb")


class _EmptyResult:
    description = None
    rowcount = -1

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list:
        return []


class Row:
    def __init__(self, names: list[str], values: Iterable[Any]) -> None:
        self._values = tuple(values)
        self._map = dict(zip(names, self._values))

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def keys(self):
        return self._map.keys()

    def __iter__(self):
        return iter(self._values)


class _Cursor:
    def __init__(self, result: Any, names: list[str]) -> None:
        self._result = result
        self._names = names
        self.rowcount = int(getattr(result, "rowcount", -1) or -1)

    def fetchone(self) -> Row | None:
        row = self._result.fetchone()
        if row is None:
            return None
        return Row(self._names, row)

    def fetchall(self) -> list[Row]:
        return [Row(self._names, row) for row in self._result.fetchall()]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class DuckConn:
    def __init__(self, path: Path) -> None:
        import duckdb

        self._con = duckdb.connect(str(path))
        self._trace = None

    def execute(self, sql: str, params: Any = None) -> _Cursor:
        if self._trace is not None:
            self._trace(sql)
        text = sql.strip()
        upper = text.upper()
        if upper.startswith("PRAGMA"):
            return _Cursor(_EmptyResult(), [])
        if upper == "BEGIN" or upper.startswith("BEGIN "):
            self._con.begin()
            return _Cursor(_EmptyResult(), [])
        if upper == "COMMIT":
            self._con.commit()
            return _Cursor(_EmptyResult(), [])
        if upper == "ROLLBACK":
            self._con.rollback()
            return _Cursor(_EmptyResult(), [])
        if params is None:
            params = []
        elif isinstance(params, tuple):
            params = list(params)
        result = self._con.execute(sql, params)
        names = [col[0] for col in (result.description or [])]
        return _Cursor(result, names)

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> _Cursor:
        if self._trace is not None:
            self._trace(sql)
        rows = list(seq)
        if not rows:
            return _Cursor(_EmptyResult(), [])
        result = self._con.executemany(sql, rows)
        names = [col[0] for col in (result.description or [])]
        return _Cursor(result, names)

    def commit(self) -> None:
        self._con.commit()

    def rollback(self) -> None:
        self._con.rollback()

    def close(self) -> None:
        self._con.close()

    def set_trace_callback(self, callback: Any) -> None:
        self._trace = callback


def _sql_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(name)
    return name


def _attach_copy(con: Any, sqlite_path: Path) -> bool:
    try:
        con.execute("INSTALL sqlite;")
        con.execute("LOAD sqlite;")
    except Exception:
        return False
    src = sqlite_path.resolve().as_posix().replace("'", "''")
    con.execute(f"ATTACH '{src}' AS src (TYPE SQLITE)")
    try:
        for table in TABLES:
            ident = _sql_ident(table)
            con.execute(f"CREATE TABLE {ident} AS SELECT * FROM src.{ident}")
    finally:
        con.execute("DETACH src")
    return True


def _chunk_copy(con: Any, sqlite_path: Path) -> None:
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    try:
        for table in TABLES:
            ident = _sql_ident(table)
            info = src.execute(f"PRAGMA table_info({ident})").fetchall()
            if not info:
                continue
            cols = ", ".join(f"{row[1]} {row[2] or 'TEXT'}" for row in info)
            con.execute(f"CREATE TABLE {ident} ({cols})")
            placeholders = ",".join("?" * len(info))
            cur = src.execute(f"SELECT * FROM {ident}")
            while True:
                chunk = cur.fetchmany(CHUNK)
                if not chunk:
                    break
                con.executemany(
                    f"INSERT INTO {ident} VALUES ({placeholders})",
                    [tuple(row) for row in chunk],
                )
            print(f"  copied {ident}", flush=True)
    finally:
        src.close()


def _add_indexes(con: Any) -> None:
    con.execute("CREATE INDEX idx_log_values_str ON log_values(identifier, str_value)")
    con.execute("CREATE INDEX idx_log_values_num ON log_values(identifier, num_value)")
    con.execute("CREATE INDEX idx_log_rows_time_abs ON log_rows(time_abs)")
    con.execute("CREATE INDEX idx_log_rows_raw ON log_rows(raw_line)")
    con.execute("CREATE INDEX idx_log_rows_source ON log_rows(source_filename)")
    con.execute("CREATE INDEX idx_custom_values_str ON custom_values(name, str_value)")


def convert_sqlite_to_duckdb(sqlite_path: Path, duck_path: Path) -> None:
    import duckdb

    tmp = duck_path.with_name(duck_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    print(f"DuckDB へ変換中: {sqlite_path.name}", flush=True)
    started = time.perf_counter()
    con = duckdb.connect(str(tmp))
    try:
        if not _attach_copy(con, sqlite_path):
            print("  sqlite 拡張が使えないため行コピーします", flush=True)
            _chunk_copy(con, sqlite_path)
        _add_indexes(con)
        con.close()
    except Exception:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    duck_path.unlink(missing_ok=True)
    tmp.replace(duck_path)
    print(f"  完了 {time.perf_counter() - started:.1f}s -> {duck_path.name}", flush=True)


def needs_convert(sqlite_path: Path, duck_path: Path) -> bool:
    return sqlite_path.is_file() and not duck_path.is_file()


def open_duckdb_file(duck_path: Path) -> DuckConn:
    return DuckConn(duck_path)


def open_or_convert(sqlite_path: Path) -> DuckConn:
    duck_path = duckdb_path_for(sqlite_path)
    if needs_convert(sqlite_path, duck_path):
        convert_sqlite_to_duckdb(sqlite_path, duck_path)
    return open_duckdb_file(duck_path)


def convert_all_cases(data_dir: Path) -> None:
    if not data_dir.exists():
        return
    for path in sorted(data_dir.glob("*.sqlite")):
        duck_path = duckdb_path_for(path)
        if needs_convert(path, duck_path):
            try:
                convert_sqlite_to_duckdb(path, duck_path)
            except Exception as exc:
                print(f"DuckDB 変換失敗 {path.name}: {exc}", flush=True)
