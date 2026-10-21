import gzip
from pathlib import Path
from access_log_db.store import create_case, open_case
from access_log_db.import_log import import_file, import_gzip_bytes

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
LINE = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1234\n'


def test_insert_then_duplicate(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    r1 = import_file(conn, "access_log", [LINE, "bad\n", LINE, "\n"])
    assert r1.read_count == 4
    assert r1.inserted_count == 1
    assert r1.discarded_count == 2
    assert r1.duplicate_count == 1
    r2 = import_file(conn, "access_log.2", [LINE])
    assert r2.inserted_count == 0
    assert r2.duplicate_count == 1
    n = conn.execute("select count(*) from log_rows").fetchone()[0]
    assert n == 1
    row = conn.execute("select time_display, time_offset, time_abs from log_rows").fetchone()
    assert row["time_display"] == "10/Oct/2024:12:00:00 +0900"
    assert row["time_offset"] == "+0900"
    assert row["time_abs"].startswith("2024-10-10T12:00:00")
    time_vals = conn.execute(
        "select count(*) from log_values where identifier='Time'"
    ).fetchone()[0]
    assert time_vals == 0
    rec = conn.execute("select * from import_files order by id").fetchall()
    assert len(rec) == 2
    conn.close()


def test_gzip(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    data = gzip.compress(LINE.encode("utf-8"))
    r = import_gzip_bytes(conn, "access_log.gz", data)
    assert r.inserted_count == 1
    conn.close()
