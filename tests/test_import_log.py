import gzip
from pathlib import Path

import pytest

from access_log_db.errors import LogFormatError
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


def test_import_persists_request_parts(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /search?q=foo HTTP/1.1" 200 1\n'
    import_file(conn, "access_log", [line])
    rows = {
        r["identifier"]: r
        for r in conn.execute(
            "SELECT identifier, is_unset, str_value FROM log_values "
            "WHERE identifier LIKE 'Request%'"
        )
    }
    assert rows["Request"]["str_value"] == "GET /search?q=foo HTTP/1.1"
    assert rows["Request-Method"]["str_value"] == "GET"
    assert rows["Request-Path"]["str_value"] == "/search"
    assert rows["Request-Query"]["str_value"] == "q=foo"
    assert rows["Request-Protocol"]["str_value"] == "HTTP/1.1"
    assert rows["Request-Query"]["is_unset"] == 0
    conn.close()


def test_import_legacy_case_skips_derived_values(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    conn.execute("DELETE FROM log_fields WHERE specifier = 'derived:%r'")
    conn.commit()
    import_file(conn, "access_log", [LINE])
    ids = {
        row["identifier"]
        for row in conn.execute(
            "SELECT identifier FROM log_values WHERE identifier LIKE 'Request%'"
        )
    }
    assert "Request" in ids
    assert "Request-Method" not in ids
    assert "Request-Path" not in ids
    assert "Request-Query" not in ids
    assert "Request-Protocol" not in ids
    conn.close()


def test_import_legacy_request_path_header_does_not_raise_dup(tmp_path: Path):
    cid = create_case(tmp_path, "t", "%h")
    conn = open_case(tmp_path, cid)
    fmt = '%r \\"%{Request-Path}i\\"'
    conn.execute("UPDATE meta SET logformat = ?", (fmt,))
    conn.execute("DELETE FROM log_fields")
    conn.executemany(
        """INSERT INTO log_fields
           (identifier, field_type, specifier, display_order, time_axis, key)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            ("Request", "string", "%r", 0, 0, "r"),
            ("Request-Path", "string", '%{Request-Path}i', 1, 0, "i"),
        ],
    )
    conn.commit()
    result = import_file(conn, "access_log", ['GET /index HTTP/1.1 "/index"\n'])
    assert result.inserted_count == 1
    ids = {row["identifier"] for row in conn.execute("SELECT identifier FROM log_values")}
    assert ids == {"Request", "Request-Path"}
    conn.close()


def test_create_case_derived_name_still_raises_dup01(tmp_path: Path):
    with pytest.raises(LogFormatError) as ei:
        create_case(tmp_path, "t", '%r \\"%{Request-Path}i\\"')
    assert ei.value.code == "E_CSE_DUP01"


def test_gzip(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    data = gzip.compress(LINE.encode("utf-8"))
    r = import_gzip_bytes(conn, "access_log.gz", data)
    assert r.inserted_count == 1
    conn.close()


from access_log_db.custom_fields import add_custom_field, set_custom_value
from access_log_db.import_log import delete_imported_file


LINE_B = '10.0.0.1 - - [10/Oct/2024:13:00:00 +0900] "GET /other HTTP/1.1" 404 9\n'


def test_delete_imported_file_removes_rows_values_and_history(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log.a", [LINE])
    import_file(conn, "access_log.b", [LINE_B])
    add_custom_field(conn, "note")
    set_custom_value(conn, 1, "note", "keep-a")
    set_custom_value(conn, 2, "note", "drop-b")

    deleted = delete_imported_file(conn, "access_log.b")

    assert deleted == 1
    names = [r[0] for r in conn.execute("SELECT source_filename FROM log_rows ORDER BY row_id")]
    assert names == ["access_log.a"]
    notes = {
        r["row_id"]: r["str_value"]
        for r in conn.execute("SELECT row_id, str_value FROM custom_values")
    }
    assert notes == {1: "keep-a"}
    vals = conn.execute(
        "SELECT COUNT(*) FROM log_values WHERE row_id = 2"
    ).fetchone()[0]
    assert vals == 0
    hist = [r[0] for r in conn.execute("SELECT source_filename FROM import_files")]
    assert hist == ["access_log.a"]
    conn.close()


def test_delete_imported_file_same_name_removes_all_history(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log", [LINE])
    import_file(conn, "access_log", [LINE_B])
    assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 2

    deleted = delete_imported_file(conn, "access_log")

    assert deleted == 2
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 0
    conn.close()


def test_delete_imported_file_keeps_row_owned_by_first_file(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log.a", [LINE])
    import_file(conn, "access_log.b", [LINE])

    deleted = delete_imported_file(conn, "access_log.b")

    assert deleted == 0
    row = conn.execute("SELECT source_filename FROM log_rows").fetchone()
    assert row["source_filename"] == "access_log.a"
    hist = [r[0] for r in conn.execute("SELECT source_filename FROM import_files")]
    assert hist == ["access_log.a"]
    conn.close()


def test_delete_imported_file_missing_or_empty_is_noop(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log", [LINE])

    assert delete_imported_file(conn, "") == 0
    assert delete_imported_file(conn, "no-such") == 0
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 1
    conn.close()
