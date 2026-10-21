from pathlib import Path
import pytest
from access_log_db.store import create_case, open_case
from access_log_db.custom_fields import add_custom_field, set_custom_value
from access_log_db.import_log import import_file
from access_log_db.aggregate import Metric, aggregate
from access_log_db.errors import AppError
from access_log_db.filters import Condition

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def _line(path: str, when: str, nbytes: str = "10") -> str:
    return f'127.0.0.1 - - [{when}] "GET {path} HTTP/1.1" 200 {nbytes}\n'


def test_hour_bucket_and_group(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "10/Oct/2024:12:10:00 +0900", "10"),
            _line("/a", "10/Oct/2024:12:50:00 +0900", "20"),
            _line("/b", "10/Oct/2024:13:10:00 +0900", "5"),
        ],
    )
    add_custom_field(conn, "機能名")
    set_custom_value(conn, 1, "機能名", "検索")
    set_custom_value(conn, 2, "機能名", "検索")
    set_custom_value(conn, 3, "機能名", "API")
    rows = aggregate(
        conn,
        [],
        group_ids=[("custom", "機能名")],
        bucket=(1, "hour"),
        metrics=[Metric("Bytes", "sum")],
    )
    assert rows[0]["機能名"] == "API" or rows[0]["機能名"] == "検索"
    by = {(r["機能名"], r["bucket_start"]): r for r in rows}
    assert by[("検索", "2024-10-10T12:00:00")]["access_count"] == 2
    assert by[("検索", "2024-10-10T12:00:00")]["Bytes_sum"] == 30
    assert by[("API", "2024-10-10T13:00:00")]["access_count"] == 1
    conn.close()


def _status_line(path: str, when: str, status: str, nbytes: str = "10") -> str:
    return f'127.0.0.1 - - [{when}] "GET {path} HTTP/1.1" {status} {nbytes}\n'


def test_unset_time_excluded_only_when_bucketed(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "10/Oct/2024:12:10:00 +0900", "10"),
            _line("/a", "10/Xxx/2024:12:10:00 +0900", "99"),
        ],
    )
    bucketed = aggregate(
        conn, [], [], bucket=(1, "hour"), metrics=[Metric("Bytes", "sum")]
    )
    assert len(bucketed) == 1
    assert list(bucketed[0].keys()) == ["bucket_start", "access_count", "Bytes_sum"]
    assert bucketed[0]["bucket_start"] == "2024-10-10T12:00:00"
    assert bucketed[0]["access_count"] == 1
    assert bucketed[0]["Bytes_sum"] == 10
    plain = aggregate(conn, [], [], bucket=None, metrics=[Metric("Bytes", "sum")])
    assert list(plain[0].keys()) == ["access_count", "Bytes_sum"]
    assert plain[0]["access_count"] == 2
    assert plain[0]["Bytes_sum"] == 109
    conn.close()


def test_zero_bytes_included_and_unset_number_skipped(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _status_line("/a", "10/Oct/2024:12:10:00 +0900", "200", "0"),
            _status_line("/a", "10/Oct/2024:12:20:00 +0900", "400", "-"),
            _status_line("/a", "10/Oct/2024:12:30:00 +0900", "-", "5"),
        ],
    )
    rows = aggregate(
        conn,
        [],
        [],
        bucket=None,
        metrics=[
            Metric("Bytes", "sum"),
            Metric("Bytes", "count"),
            Metric("Status", "sum"),
            Metric("Status", "count"),
            Metric("Status", "avg"),
        ],
    )
    assert list(rows[0].keys()) == [
        "access_count",
        "Bytes_sum",
        "Bytes_count",
        "Status_sum",
        "Status_count",
        "Status_avg",
    ]
    assert rows[0]["access_count"] == 3
    assert rows[0]["Bytes_sum"] == 5
    assert rows[0]["Bytes_count"] == 3
    assert rows[0]["Status_sum"] == 600
    assert rows[0]["Status_count"] == 2
    assert rows[0]["Status_avg"] == 300
    zeros = aggregate(
        conn,
        [Condition("Bytes", "log", "number", "eq", "0")],
        [],
        bucket=None,
        metrics=[Metric("Bytes", "sum")],
    )
    assert zeros[0]["access_count"] == 2
    assert zeros[0]["Bytes_sum"] == 0
    conn.close()


def test_string_count_skips_unset_custom(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "10/Oct/2024:12:10:40 +0900"),
            _line("/b", "10/Oct/2024:12:50:00 +0900"),
            _line("/a", "10/Oct/2024:13:10:00 +0900"),
        ],
    )
    add_custom_field(conn, "機能名")
    set_custom_value(conn, 1, "機能名", "検索")
    set_custom_value(conn, 2, "機能名", "")
    rows = aggregate(
        conn,
        [Condition("Request", "log", "string", "contains", "/a")],
        group_ids=[("log", "Request"), ("custom", "機能名")],
        bucket=(30, "second"),
        metrics=[Metric("機能名", "count")],
    )
    assert list(rows[0].keys()) == [
        "Request",
        "機能名",
        "bucket_start",
        "access_count",
        "機能名_count",
    ]
    by = {(r["Request"], r["bucket_start"]): r for r in rows}
    first = by[("GET /a HTTP/1.1", "2024-10-10T12:10:30")]
    assert first["機能名"] == "検索"
    assert first["access_count"] == 1
    assert first["機能名_count"] == 1
    second = by[("GET /a HTTP/1.1", "2024-10-10T13:10:00")]
    assert second["機能名"] is None
    assert second["access_count"] == 1
    assert second["機能名_count"] == 0
    conn.close()


def test_minute_bucket_floors_wall_clock(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "10/Oct/2024:12:10:00 +0900", "1"),
            _line("/a", "10/Oct/2024:12:50:00 +0900", "1"),
            _line("/a", "10/Oct/2024:13:10:00 +0900", "1"),
        ],
    )
    rows = aggregate(
        conn, [], [], bucket=(15, "minute"), metrics=[Metric("Bytes", "sum")]
    )
    by = {r["bucket_start"]: r["access_count"] for r in rows}
    assert by == {
        "2024-10-10T12:00:00": 1,
        "2024-10-10T12:45:00": 1,
        "2024-10-10T13:00:00": 1,
    }
    wide = aggregate(conn, [], [], bucket=(2, "hour"), metrics=[])
    assert [(r["bucket_start"], r["access_count"]) for r in wide] == [
        ("2024-10-10T12:00:00", 3)
    ]
    conn.close()


def test_keyset_pages_and_negative_offset(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("access_log_db.aggregate._PAGE", 1)
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "10/Oct/2024:12:10:00 +0900", "10"),
            _line("/a", "10/Oct/2024:12:40:00 -0500", "7"),
            _line("/a", "10/Xxx/2024:12:10:00 +0900", "99"),
        ],
    )
    rows = aggregate(
        conn, [], [], bucket=(1, "hour"), metrics=[Metric("Bytes", "sum")]
    )
    assert len(rows) == 1
    assert rows[0]["bucket_start"] == "2024-10-10T12:00:00"
    assert rows[0]["access_count"] == 2
    assert rows[0]["Bytes_sum"] == 17
    later = aggregate(
        conn,
        [Condition("Time", "log", "datetime", "after", "2024-10-10T12:30")],
        [],
        bucket=(1, "hour"),
        metrics=[Metric("Bytes", "sum")],
    )
    assert later[0]["access_count"] == 1
    assert later[0]["Bytes_sum"] == 7
    assert later[0]["bucket_start"] == "2024-10-10T12:00:00"
    conn.close()


def test_bucket_errors(tmp_path: Path):
    cid = create_case(tmp_path, "t", "%h %>s %b")
    conn = open_case(tmp_path, cid)
    with pytest.raises(AppError) as missing_time:
        aggregate(conn, [], [], bucket=(1, "hour"), metrics=[])
    assert missing_time.value.code == "E_WRK_CMB01"
    assert missing_time.value.message == "この案件では時間集計を使えません。"
    conn.close()

    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    with pytest.raises(AppError) as bad_n:
        aggregate(conn, [], [], bucket=(0, "hour"), metrics=[])
    assert bad_n.value.code == "E_WRK_RNG01"
    assert bad_n.value.message == "n は1以上の整数を指定してください。"
    with pytest.raises(AppError) as negative:
        aggregate(conn, [], [], bucket=(-1, "minute"), metrics=[])
    assert negative.value.code == "E_WRK_RNG01"
    with pytest.raises(AppError) as fractional:
        aggregate(conn, [], [], bucket=(1.5, "second"), metrics=[])
    assert fractional.value.code == "E_WRK_RNG01"
    conn.close()


def test_colliding_row_id_custom_keeps_bucket_count(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "10/Oct/2024:12:10:00 +0900", "10"),
            _line("/b", "10/Oct/2024:13:10:00 +0900", "5"),
        ],
    )
    conn.execute(
        "INSERT INTO custom_fields (name, display_order) VALUES ('row_id', 0)"
    )
    conn.executemany(
        """INSERT INTO custom_values (row_id, name, is_unset, str_value)
           VALUES (?, 'row_id', 0, ?)""",
        [(1, "not-an-id"), (2, "also-not")],
    )
    conn.commit()
    rows = aggregate(conn, [], [], bucket=(1, "hour"), metrics=[])
    assert sum(r["access_count"] for r in rows) == 2
    conn.close()
