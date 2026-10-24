import pytest

from logcase.filters import Condition, count_rows, fetch_rows
from logcase.import_log import import_file
from logcase.store import create_case, open_case

pytest.importorskip("duckdb")

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def test_duckdb_open_and_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGCASE_ENGINE", "duckdb")
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /admin HTTP/1.1" 200 10\n',
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "POST /other HTTP/1.1" 200 10\n',
        ],
    )
    conds = [Condition("Request-Path", "log", "string", "starts", "/admin")]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert rows[0]["Request-Path"] == "/admin"
    assert (tmp_path / f"{cid}.duckdb").is_file()
    conn.close()
