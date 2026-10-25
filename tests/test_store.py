from pathlib import Path
from uuid import uuid1, uuid4

import pytest
from logcase.errors import AppError
from logcase.store import create_case, list_cases, open_case, rename_case, delete_case

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def test_create_list_rename_delete(tmp_path: Path):
    cid = create_case(tmp_path, "  案件A  ", COMMON)
    assert len(cid) == 36
    assert (tmp_path / f"{cid}.duckdb").is_file()
    rows = list_cases(tmp_path)
    assert len(rows) == 1
    assert rows[0]["name"] == "案件A"
    assert rows[0]["id"] == cid
    assert rows[0]["row_count"] == 0
    assert rows[0]["file_count"] == 0
    rename_case(tmp_path, cid, "案件B")
    assert list_cases(tmp_path)[0]["name"] == "案件B"
    delete_case(tmp_path, cid)
    assert list_cases(tmp_path) == []
    assert not (tmp_path / f"{cid}.duckdb").exists()


def test_missing_case(tmp_path: Path):
    with pytest.raises(AppError) as ei:
        rename_case(tmp_path, "00000000-0000-0000-0000-000000000000", "x")
    assert ei.value.code == "E_CSE_EXS01"


def test_list_cases_unreadable_v4_file_sets_error(tmp_path: Path):
    case_id = str(uuid4())
    path = tmp_path / f"{case_id}.duckdb"
    path.write_bytes(b"not a database")

    rows = list_cases(tmp_path)

    assert rows == [{"id": case_id, "error": path.name}]


def test_list_cases_skip_ids_does_not_open_running_file(tmp_path: Path):
    cid = create_case(tmp_path, "案件A", COMMON)
    path = tmp_path / f"{cid}.duckdb"
    path.write_bytes(b"not a database")
    other = create_case(tmp_path, "案件B", COMMON)

    rows = list_cases(tmp_path, skip_ids={cid})

    by_id = {row["id"]: row for row in rows}
    assert by_id[cid] == {"id": cid, "skipped": True, "error": None}
    assert by_id[other]["name"] == "案件B"
    assert by_id[other]["error"] is None


def test_list_cases_skips_non_v4_name(tmp_path: Path):
    cid = create_case(tmp_path, "案件A", COMMON)
    v1 = str(uuid1())
    src = tmp_path / f"{cid}.duckdb"
    dest = tmp_path / f"{v1}.duckdb"
    src.rename(dest)

    rows = list_cases(tmp_path)

    assert rows == [{"id": v1, "error": dest.name}]


def test_open_rename_reject_non_v4_and_hyphenless(tmp_path: Path):
    v1 = str(uuid1())
    bare = uuid4().hex
    for case_id in (v1, bare):
        (tmp_path / f"{case_id}.duckdb").write_bytes(b"x")
        with pytest.raises(AppError) as opened:
            open_case(tmp_path, case_id)
        assert opened.value.code == "E_CSE_EXS01"
        with pytest.raises(AppError) as renamed:
            rename_case(tmp_path, case_id, "x")
        assert renamed.value.code == "E_CSE_EXS01"
        with pytest.raises(AppError) as deleted:
            delete_case(tmp_path, case_id)
        assert deleted.value.code == "E_CSE_EXS01"
        assert (tmp_path / f"{case_id}.duckdb").is_file()


def test_log_fields_persisted(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    ids = [r["identifier"] for r in conn.execute(
        "select identifier from log_fields order by display_order"
    )]
    assert ids == [
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
    conn.close()


def test_create_case_has_time_index(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    names = {
        row[0]
        for row in conn.execute("SELECT index_name FROM duckdb_indexes()")
    }
    assert "idx_log_rows_time_abs" in names
    conn.close()
