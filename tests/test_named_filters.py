from pathlib import Path
import pytest
from access_log_db.errors import AppError
from access_log_db.store import create_case, open_case
from access_log_db.named_filters import (
    save_named_filter,
    load_named_filter,
    list_named_filters,
)

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
SPEC = [{"identifier": "Status", "source": "log", "field_type": "number", "op": "eq", "value": "200"}]


def test_save_load_overwrite(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    save_named_filter(conn, "  成功  ", SPEC, overwrite=False)
    assert list_named_filters(conn) == ["成功"]
    assert load_named_filter(conn, "成功") == SPEC
    with pytest.raises(AppError) as ei:
        save_named_filter(conn, "成功", SPEC, overwrite=False)
    assert ei.value.code == "W_WRK_BIZ03"
    save_named_filter(
        conn,
        "成功",
        [{**SPEC[0], "value": "404"}],
        overwrite=True,
    )
    assert load_named_filter(conn, "成功")[0]["value"] == "404"
    with pytest.raises(AppError) as ei:
        load_named_filter(conn, "無い")
    assert ei.value.code == "E_WRK_EXS01"
    conn.close()
