from pathlib import Path
import pytest
from access_log_db.errors import AppError
from access_log_db.store import create_case, open_case
from access_log_db.custom_fields import (
    add_custom_field,
    set_custom_value,
    bulk_set_custom,
)
from access_log_db.import_log import import_file
from access_log_db.filters import Condition, fetch_rows

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
LINE = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET {path} HTTP/1.1" 200 1\n'


def test_add_and_bulk(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [LINE.format(path="/a"), LINE.format(path="/b"), LINE.format(path="/c")],
    )
    add_custom_field(conn, "  機能名  ")
    with pytest.raises(AppError) as ei:
        add_custom_field(conn, "機能名")
    assert ei.value.code == "E_WRK_DUP01"
    with pytest.raises(AppError) as ei:
        add_custom_field(conn, "Status")
    assert ei.value.code == "E_WRK_DUP02"
    conds = [Condition("Request", "log", "string", "contains", "/a")]
    n = bulk_set_custom(conn, "機能名", "検索", conds)
    assert n == 1
    rows = fetch_rows(conn, [], 0)
    by_path = {r["Request"]: r["機能名"] for r in rows}
    assert by_path["GET /a HTTP/1.1"] == "検索"
    assert by_path["GET /b HTTP/1.1"] is None
    bulk_set_custom(conn, "機能名", "", [])
    rows = fetch_rows(conn, [], 0)
    assert all(r["機能名"] is None for r in rows)
    set_custom_value(conn, 2, "機能名", "単独")
    rows = fetch_rows(conn, [], 0)
    assert rows[1]["機能名"] == "単独"
    conn.close()


def test_row_id_is_reserved(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    with pytest.raises(AppError) as ei:
        add_custom_field(conn, "row_id")
    assert ei.value.code == "E_WRK_DUP02"
    conn.close()
