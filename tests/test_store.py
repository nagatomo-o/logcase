from pathlib import Path
import pytest
from access_log_db.errors import AppError
from access_log_db.store import create_case, list_cases, rename_case, delete_case

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def test_create_list_rename_delete(tmp_path: Path):
    cid = create_case(tmp_path, "  案件A  ", COMMON)
    assert len(cid) == 36
    assert (tmp_path / f"{cid}.sqlite").is_file()
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
    assert not (tmp_path / f"{cid}.sqlite").exists()


def test_missing_case(tmp_path: Path):
    with pytest.raises(AppError) as ei:
        rename_case(tmp_path, "00000000-0000-0000-0000-000000000000", "x")
    assert ei.value.code == "E_CSE_EXS01"


def test_log_fields_persisted(tmp_path: Path):
    from access_log_db.store import open_case
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    ids = [r["identifier"] for r in conn.execute(
        "select identifier from log_fields order by display_order"
    )]
    assert ids == [
        "RemoteHost", "RemoteLogName", "RemoteUser", "Time", "Request", "Status", "Bytes",
    ]
    conn.close()
