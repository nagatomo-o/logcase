"""Compare starts-filter SQL on 5M synthetic Request-Path rows."""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

N = 5_000_000
PREFIX = "/admin"

OLD_SQL = (
    "SELECT COUNT(*) FROM log_rows r WHERE EXISTS ("
    "SELECT 1 FROM log_values v WHERE v.row_id = r.row_id "
    "AND v.identifier = ? AND v.str_value IS NOT NULL "
    "AND substr(v.str_value, 1, length(?)) = ?)"
)
NEW_SQL = (
    "SELECT COUNT(*) FROM log_rows r WHERE r.row_id IN ("
    "SELECT v.row_id FROM log_values v "
    "WHERE v.identifier = ? AND v.str_value >= ? AND v.str_value < ?)"
)


def timed(label: str, fn):
    started = time.perf_counter()
    value = fn()
    elapsed = time.perf_counter() - started
    print(f"{label}: {elapsed:.3f}s  result={value}", flush=True)
    return elapsed, value


def bench_duckdb(n: int) -> None:
    import duckdb

    con = duckdb.connect()
    print(f"DuckDB: generate {n} rows", flush=True)
    con.execute(
        f"CREATE TABLE log_rows AS SELECT i AS row_id FROM generate_series(1, {n}) t(i)"
    )
    con.execute(
        f"""
        CREATE TABLE log_values AS
        SELECT
          i AS row_id,
          'Request-Path' AS identifier,
          0 AS is_unset,
          CASE WHEN i % 100 = 0 THEN '/admin/' || i ELSE '/other/' || i END AS str_value,
          NULL AS num_value
        FROM generate_series(1, {n}) t(i)
        """
    )
    con.execute("CREATE INDEX idx_log_values_str ON log_values(identifier, str_value)")
    timed(
        "DuckDB 旧SQL (EXISTS+substr)",
        lambda: con.execute(OLD_SQL, ["Request-Path", PREFIX, PREFIX]).fetchone()[0],
    )
    timed(
        "DuckDB 新SQL (IN+接頭辞範囲)",
        lambda: con.execute(NEW_SQL, ["Request-Path", PREFIX, "/admio"]).fetchone()[0],
    )
    timed(
        "DuckDB 新SQL 2回目",
        lambda: con.execute(NEW_SQL, ["Request-Path", PREFIX, "/admio"]).fetchone()[0],
    )
    con.close()


def bench_sqlite(n: int) -> None:
    tmp = Path(tempfile.mkdtemp()) / "bench.sqlite"
    print(f"SQLite: generate {n} rows -> {tmp}", flush=True)
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("CREATE TABLE log_rows (row_id INTEGER PRIMARY KEY)")
    con.execute(
        "CREATE TABLE log_values ("
        "row_id INTEGER, identifier TEXT, is_unset INTEGER, "
        "str_value TEXT, num_value INTEGER)"
    )
    con.execute("BEGIN")
    batch = []
    for i in range(1, n + 1):
        path = f"/admin/{i}" if i % 100 == 0 else f"/other/{i}"
        batch.append((i, "Request-Path", 0, path, None))
        if len(batch) >= 20_000:
            con.executemany(
                "INSERT INTO log_rows(row_id) VALUES (?)",
                [(row[0],) for row in batch],
            )
            con.executemany(
                "INSERT INTO log_values VALUES (?,?,?,?,?)",
                batch,
            )
            batch.clear()
    if batch:
        con.executemany(
            "INSERT INTO log_rows(row_id) VALUES (?)",
            [(row[0],) for row in batch],
        )
        con.executemany("INSERT INTO log_values VALUES (?,?,?,?,?)", batch)
    con.execute("CREATE INDEX idx_log_values_str ON log_values(identifier, str_value)")
    con.commit()
    timed(
        "SQLite 旧SQL (EXISTS+substr)",
        lambda: con.execute(OLD_SQL, ["Request-Path", PREFIX, PREFIX]).fetchone()[0],
    )
    timed(
        "SQLite 新SQL (IN+接頭辞範囲)",
        lambda: con.execute(NEW_SQL, ["Request-Path", PREFIX, "/admio"]).fetchone()[0],
    )
    timed(
        "SQLite 新SQL 2回目",
        lambda: con.execute(NEW_SQL, ["Request-Path", PREFIX, "/admio"]).fetchone()[0],
    )
    con.close()


if __name__ == "__main__":
    bench_duckdb(N)
    print("---", flush=True)
    bench_sqlite(min(N, 1_000_000))
