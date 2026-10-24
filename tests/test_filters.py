from pathlib import Path
from logcase.custom_fields import add_custom_field, set_custom_value
from logcase.store import create_case, open_case
from logcase.import_log import import_file
from logcase.filters import Condition, count_rows, fetch_rows, _one

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


def test_fetch_rows_batches_value_queries(tmp_path: Path):
    conn = _conn(tmp_path)
    add_custom_field(conn, "note")
    set_custom_value(conn, 2, "note", "batched")
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    rows = fetch_rows(conn, [], after_row_id=0)

    conn.set_trace_callback(None)
    log_queries = [
        sql for sql in statements
        if "FROM log_values" in sql and sql.lstrip().upper().startswith("SELECT")
    ]
    custom_queries = [
        sql for sql in statements
        if "FROM custom_values" in sql and sql.lstrip().upper().startswith("SELECT")
    ]
    assert len(log_queries) == 1
    assert " IN (" in log_queries[0]
    assert len(custom_queries) == 1
    assert " IN (" in custom_queries[0]
    assert rows[1]["note"] == "batched"
    conn.close()


def test_filter_contains_request_path(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Request-Path", "log", "string", "contains", "/api")]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert rows[0]["Request-Path"] == "/api"
    assert rows[0]["Request-Query"] == ""
    conn.close()


def test_datetime_on_or_after(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Time", "log", "datetime", "on_or_after", "2024-10-10T13:00:00")]
    assert count_rows(conn, conds) == 2
    conn.close()


def test_not_in_excludes_listed_request_methods(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "200"),
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "POST /b HTTP/1.1" 200 10\n',
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "HEAD /c HTTP/1.1" 200 10\n',
        ],
    )
    conds = [
        Condition("Request-Method", "log", "string", "not_in", ["GET", "HEAD"]),
    ]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert rows[0]["Request-Method"] == "POST"
    conn.close()


def test_not_in_keeps_unset_custom_values(tmp_path: Path):
    conn = _conn(tmp_path)
    add_custom_field(conn, "note")
    set_custom_value(conn, 2, "note", "bot")
    conds = [Condition("note", "custom", "string", "not_in", ["bot"])]
    assert count_rows(conn, conds) == 2
    ids = [r["row_id"] for r in fetch_rows(conn, conds, after_row_id=0)]
    assert ids == [1, 3]
    conn.close()


def test_not_in_empty_list_matches_all(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Request-Method", "log", "string", "not_in", [])]
    assert count_rows(conn, conds) == 3
    conn.close()


def test_string_eq_request_method(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "200"),
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "POST /b HTTP/1.1" 200 10\n',
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "HEAD /c HTTP/1.1" 200 10\n',
        ],
    )
    conds = [Condition("Request-Method", "log", "string", "eq", "GET")]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert rows[0]["Request-Method"] == "GET"
    ne = [Condition("Request-Method", "log", "string", "ne", "GET")]
    assert count_rows(conn, ne) == 2
    methods = {r["Request-Method"] for r in fetch_rows(conn, ne, after_row_id=0)}
    assert methods == {"POST", "HEAD"}
    conn.close()


def test_string_ne_keeps_unset_custom_values(tmp_path: Path):
    conn = _conn(tmp_path)
    add_custom_field(conn, "note")
    set_custom_value(conn, 2, "note", "bot")
    conds = [Condition("note", "custom", "string", "ne", "bot")]
    assert count_rows(conn, conds) == 2
    ids = [r["row_id"] for r in fetch_rows(conn, conds, after_row_id=0)]
    assert ids == [1, 3]
    conn.close()


def test_string_eq_keeps_comma_in_value(tmp_path: Path):
    conn = _conn(tmp_path)
    add_custom_field(conn, "note")
    set_custom_value(conn, 1, "note", "a,b")
    set_custom_value(conn, 2, "note", "a")
    conds = [Condition("note", "custom", "string", "eq", "a,b")]
    assert count_rows(conn, conds) == 1
    assert fetch_rows(conn, conds, after_row_id=0)[0]["row_id"] == 1
    conn.close()


def test_string_eq_empty_and_ne_complement(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "200"),
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /b?x=1 HTTP/1.1" 200 10\n',
        ],
    )
    eq = [Condition("Request-Query", "log", "string", "eq", "")]
    assert count_rows(conn, eq) == 1
    assert fetch_rows(conn, eq, after_row_id=0)[0]["Request-Path"] == "/a"
    ne = [Condition("Request-Query", "log", "string", "ne", "")]
    assert count_rows(conn, ne) == 1
    assert fetch_rows(conn, ne, after_row_id=0)[0]["Request-Query"] == "x=1"
    conn.close()


def test_datetime_after_excludes_exact_wall_second(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Time", "log", "datetime", "after", "2024-10-10T12:00:00")]
    assert count_rows(conn, conds) == 2
    ids = [r["row_id"] for r in fetch_rows(conn, conds, after_row_id=0)]
    assert ids == [2, 3]
    conn.close()


def test_time_on_or_after_and_request_method_eq(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "200"),
            '127.0.0.1 - - [10/Oct/2024:13:00:00 +0900] "POST /b HTTP/1.1" 200 10\n',
            '127.0.0.1 - - [11/Oct/2024:12:00:00 +0900] "GET /c HTTP/1.1" 200 10\n',
        ],
    )
    conds = [
        Condition("Time", "log", "datetime", "on_or_after", "2024-10-10T13:00:00"),
        Condition("Request-Method", "log", "string", "eq", "POST"),
    ]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert rows[0]["Request-Method"] == "POST"
    assert rows[0]["Request-Path"] == "/b"
    conn.close()


def test_string_eq_sql_uses_in_subquery():
    sql, params = _one(Condition("Request-Method", "log", "string", "eq", "POST"))
    assert "row_id IN (SELECT" in sql
    assert "EXISTS" not in sql
    assert params == ["Request-Method", "POST"]


def test_starts_request_path(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Request-Path", "log", "string", "starts", "/in")]
    assert count_rows(conn, conds) == 2
    paths = {row["Request-Path"] for row in fetch_rows(conn, conds, after_row_id=0)}
    assert paths == {"/index"}
    none = [Condition("Request-Path", "log", "string", "not_starts", "/in")]
    assert count_rows(conn, none) == 1
    assert fetch_rows(conn, none, after_row_id=0)[0]["Request-Path"] == "/api"
    conn.close()


def test_starts_sql_uses_prefix_range():
    sql, params = _one(Condition("Request-Path", "log", "string", "starts", "/admin"))
    assert "row_id IN (SELECT" in sql
    assert "substr(" not in sql
    assert params == ["Request-Path", "/admin", "/admio"]
