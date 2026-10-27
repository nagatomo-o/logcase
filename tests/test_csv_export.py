import csv
import io
from pathlib import Path

import pytest

from logcase.custom_fields import add_custom_field, set_custom_value
from logcase.errors import AppError
from logcase.filters import Condition
from logcase.import_log import import_file
from logcase.messages import MSG
from logcase.store import create_case, open_case
from logcase.csv_export import (
    aggregate_csv_bytes,
    rows_csv_bytes,
    write_aggregate_csv,
    write_rows_csv,
)

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
LINE = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1234\n'
LOG_HEADERS = [
    "RemoteHost",
    "RemoteLogName",
    "RemoteUser",
    "Time",
    "Request",
    "Request-Method",
    "Request-Path",
    "Request-Query",
    "Request-Protocol",
    "Status",
    "Bytes",
]


def test_rows_csv_bom_and_crlf(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log", [LINE])
    out = tmp_path / "out.csv"
    n = write_rows_csv(conn, [], out)
    assert n == 1
    data = out.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert "\r\n" in text
    header = text.split("\r\n")[0]
    assert header.startswith("行ID,元ファイル名,ファイル内行番号,")
    assert "RemoteHost" in header
    assert "Time" in header
    conn.close()


def test_aggregate_csv(tmp_path: Path):
    out = tmp_path / "agg.csv"
    n = write_aggregate_csv(
        [{"機能名": "検索", "access_count": 2, "Bytes_sum": 30}],
        out,
    )
    assert n == 1
    text = out.read_bytes().decode("utf-8-sig")
    assert text.startswith("機能名,access_count,Bytes_sum\r\n")


def test_rows_csv_columns_custom_and_unset(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            LINE,
            '127.0.0.1 - - [10/Oct/2024:12:00:01 +0900] "GET /b HTTP/1.1" 404 5\n',
        ],
    )
    add_custom_field(conn, "機能名")
    add_custom_field(conn, "担当")
    set_custom_value(conn, 1, "機能名", "検索")
    out = tmp_path / "out.csv"
    n = write_rows_csv(conn, [], out)
    assert n == 2
    rows = list(csv.reader(io.StringIO(out.read_bytes().decode("utf-8-sig"))))
    assert rows[0][:3] == ["行ID", "元ファイル名", "ファイル内行番号"]
    assert rows[0][3:14] == LOG_HEADERS
    assert rows[0][14:] == ["機能名", "担当"]
    assert rows[1][0] == "1"
    assert rows[1][1] == "access_log"
    assert rows[1][2] == "1"
    assert rows[1][3] == "127.0.0.1"
    assert rows[1][6] == "10/Oct/2024:12:00:00 +0900"
    assert rows[1][7] == "GET /index HTTP/1.1"
    assert rows[1][8] == "GET"
    assert rows[1][9] == "/index"
    assert rows[1][10] == ""
    assert rows[1][11] == "HTTP/1.1"
    assert rows[1][12] == "200"
    assert rows[1][13] == "1234"
    assert rows[1][14] == "検索"
    assert rows[1][15] == ""
    assert rows[2][14] == ""
    assert rows[2][15] == ""
    conn.close()


def test_rows_csv_quotes_comma_and_quote(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /a,b\\"c HTTP/1.1" 200 1\n',
        ],
    )
    out = tmp_path / "out.csv"
    write_rows_csv(conn, [], out)
    text = out.read_bytes().decode("utf-8-sig")
    assert '"GET /a,b""c HTTP/1.1"' in text
    assert "\n" not in text.replace("\r\n", "")
    conn.close()


def test_rows_csv_respects_conditions(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            LINE,
            '127.0.0.1 - - [10/Oct/2024:12:00:01 +0900] "GET /only HTTP/1.1" 200 9\n',
        ],
    )
    out = tmp_path / "out.csv"
    conds = [Condition("Request", "log", "string", "contains", "/only")]
    n = write_rows_csv(conn, conds, out)
    assert n == 1
    rows = list(csv.reader(io.StringIO(out.read_bytes().decode("utf-8-sig"))))
    assert len(rows) == 2
    assert "/only" in rows[1][7]
    conn.close()


def test_rows_csv_pages_past_5000(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    lines = [
        f'127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /p{i} HTTP/1.1" 200 1\n'
        for i in range(5001)
    ]
    import_file(conn, "access_log", lines)
    out = tmp_path / "out.csv"
    n = write_rows_csv(conn, [], out)
    assert n == 5001
    body = [
        line
        for line in out.read_bytes().decode("utf-8-sig").split("\r\n")
        if line
    ]
    assert len(body) == 5002
    assert body[1].startswith("1,access_log,1,")
    assert body[-1].startswith("5001,access_log,5001,")
    conn.close()


def test_rows_failure_deletes_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log", [LINE])
    out = tmp_path / "out.csv"
    out.write_bytes(b"partial")

    def boom(*args, **kwargs):
        raise OSError("disk")

    monkeypatch.setattr("logcase.csv_export.fetch_rows", boom)
    with pytest.raises(AppError) as ei:
        write_rows_csv(conn, [], out)
    assert ei.value.code == "E_EXP_SYS01"
    assert ei.value.message == MSG["E_EXP_SYS01"]
    assert not out.exists()
    conn.close()


def test_aggregate_blank_none_and_empty(tmp_path: Path):
    out = tmp_path / "agg.csv"
    n = write_aggregate_csv(
        [{"機能名": None, "access_count": 2, "Bytes_sum": None}],
        out,
    )
    assert n == 1
    text = out.read_bytes().decode("utf-8-sig")
    assert text == "機能名,access_count,Bytes_sum\r\n,2,\r\n"
    empty = tmp_path / "empty.csv"
    assert write_aggregate_csv([], empty) == 0
    assert empty.read_bytes() == b""


def test_aggregate_failure_leaves_no_file(tmp_path: Path):
    out = tmp_path / "missing" / "agg.csv"
    with pytest.raises(AppError) as ei:
        write_aggregate_csv([{"access_count": 1}], out)
    assert ei.value.code == "E_EXP_SYS01"
    assert ei.value.message == MSG["E_EXP_SYS01"]
    assert not out.exists()


def test_custom_field_named_row_id_keeps_integer_first_column(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log", [LINE])
    try:
        add_custom_field(conn, "行ID")
    except AppError:
        order = conn.execute(
            "SELECT COALESCE(MAX(display_order), -1) + 1 FROM custom_fields"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO custom_fields (name, display_order) VALUES ('行ID', ?)",
            (order,),
        )
        conn.commit()
    set_custom_value(conn, 1, "行ID", "not-the-row-id")
    rows = list(
        csv.reader(io.StringIO(rows_csv_bytes(conn, []).decode("utf-8-sig")))
    )
    assert rows[1][0] == "1"
    assert rows[1][1] == "access_log"
    assert rows[1][2] == "1"
    assert rows[0][-1] == "行ID"
    assert rows[1][-1] == "not-the-row-id"
    conn.close()


def test_csv_bytes_bom_crlf(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log", [LINE])
    data = rows_csv_bytes(conn, [])
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert text.startswith("行ID,元ファイル名,ファイル内行番号,")
    assert "\r\n" in text
    blob = aggregate_csv_bytes([{"機能名": "検索", "access_count": 2}])
    assert blob.startswith(b"\xef\xbb\xbf")
    assert blob.decode("utf-8-sig") == "機能名,access_count\r\n検索,2\r\n"
    conn.close()


def test_rows_csv_follows_time_desc_sort(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            '127.0.0.1 - - [11/Oct/2024:12:00:00 +0900] "GET /late HTTP/1.1" 200 10\n',
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /early HTTP/1.1" 200 10\n',
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /same HTTP/1.1" 200 10\n',
        ],
    )
    text = rows_csv_bytes(conn, [], sort="time_desc").decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [r["Request-Path"] for r in rows] == ["/late", "/same", "/early"]
    conn.close()
