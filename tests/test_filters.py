from pathlib import Path
from access_log_db.store import create_case, open_case
from access_log_db.import_log import import_file
from access_log_db.filters import Condition, count_rows, fetch_rows

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def _line(path: str, status: str, when: str = "10/Oct/2024:12:00:00 +0900") -> str:
    return f'127.0.0.1 - - [{when}] "GET {path} HTTP/1.1" {status} 10\n'


def _conn(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/index", "200"),
            _line("/api", "404", "10/Oct/2024:13:00:00 +0900"),
            _line("/index", "500", "11/Oct/2024:12:00:00 +0900"),
        ],
    )
    return conn


def test_contains_and_status(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [
        Condition("Request", "log", "string", "contains", "/index"),
        Condition("Status", "log", "number", "eq", "200"),
    ]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert len(rows) == 1
    assert rows[0]["row_id"] == 1
    assert rows[0]["Request"] == "GET /index HTTP/1.1"
    assert rows[0]["Status"] == 200
    conn.close()


def test_keyset(tmp_path: Path):
    conn = _conn(tmp_path)
    page1 = fetch_rows(conn, [], after_row_id=0, limit=2)
    assert [r["row_id"] for r in page1] == [1, 2]
    page2 = fetch_rows(conn, [], after_row_id=2, limit=2)
    assert [r["row_id"] for r in page2] == [3]
    conn.close()


def test_datetime_on_or_after(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Time", "log", "datetime", "on_or_after", "2024-10-10T13:00:00")]
    assert count_rows(conn, conds) == 2
    conn.close()
