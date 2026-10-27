from pathlib import Path

import pytest

from logcase.errors import AppError
from logcase.named_aggregates import (
    delete_named_aggregate,
    list_named_aggregates,
    load_named_aggregate,
    save_named_aggregate,
)
from logcase.store import create_case, open_case

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
SPEC = {
    "groups": ["log:Status"],
    "use_bucket": True,
    "n": "10",
    "unit": "minute",
    "metric_id": "Bytes",
    "metric_fn": "sum",
}


def test_save_load_overwrite_delete(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    save_named_aggregate(conn, "  ステータス  ", SPEC, overwrite=False)
    assert list_named_aggregates(conn) == ["ステータス"]
    assert load_named_aggregate(conn, "ステータス") == SPEC
    with pytest.raises(AppError) as ei:
        save_named_aggregate(conn, "ステータス", SPEC, overwrite=False)
    assert ei.value.code == "W_WRK_BIZ07"
    save_named_aggregate(
        conn, "ステータス", {**SPEC, "n": "5"}, overwrite=True
    )
    assert load_named_aggregate(conn, "ステータス")["n"] == "5"
    with pytest.raises(AppError) as ei:
        load_named_aggregate(conn, "無い")
    assert ei.value.code == "E_WRK_EXS02"
    delete_named_aggregate(conn, "ステータス")
    assert list_named_aggregates(conn) == []
    conn.close()
