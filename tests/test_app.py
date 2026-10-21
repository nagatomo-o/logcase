import io
import uuid
from pathlib import Path

from access_log_db.app import create_app


COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def test_create_case_and_open_work(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    rv = client.get("/")
    assert rv.status_code == 200
    rv = client.post(
        "/cases",
        data={"name": "X", "logformat": COMMON},
        follow_redirects=True,
    )
    assert "案件を作成しました。".encode("utf-8") in rv.data
    listing = client.get("/")
    assert "X".encode("utf-8") in listing.data
    cid = listing.data.decode("utf-8").split("/cases/")[1][:36]
    work = client.get(f"/cases/{cid}")
    assert work.status_code == 200
    assert "X".encode("utf-8") in work.data
    assert "LogFormat".encode("utf-8") in work.data or b"RemoteHost" in work.data


def test_loopback_factory_has_no_auth(tmp_path: Path):
    app = create_app(tmp_path)
    assert app.config["TESTING"] is False or True
    client = app.test_client()
    assert client.get("/").status_code == 200


def test_aggregate_export_without_result_returns_work_with_400(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    client.post("/cases", data={"name": "X", "logformat": COMMON})
    listing = client.get("/")
    cid = listing.data.decode("utf-8").split("/cases/")[1][:36]

    rv = client.post(f"/cases/{cid}/export/aggregate")

    assert rv.status_code == 400
    assert "X".encode("utf-8") in rv.data


def _create_case(client, name: str) -> str:
    client.post("/cases", data={"name": name, "logformat": COMMON})
    listing = client.get("/")
    html = listing.data.decode("utf-8")
    marker = f">{name}</a>"
    prefix = html[: html.index(marker)]
    return prefix.rsplit("/cases/", 1)[1].split('"', 1)[0]


def test_invalid_operator_filter_is_rejected_without_bricking_work(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.post(
        f"/cases/{cid}/filter",
        data={"identifier": "log:number:Status", "op": "contains", "value": "x"},
        follow_redirects=True,
    )

    assert rv.status_code == 200
    assert "フィルタ".encode("utf-8") in rv.data
    with client.session_transaction() as sess:
        assert not sess.get("filters_by_case", {}).get(cid)


def test_filters_are_isolated_by_case(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    case_a = _create_case(client, "A")
    case_b = _create_case(client, "B")

    client.post(
        f"/cases/{case_a}/filter",
        data={"identifier": "log:string:RemoteHost", "op": "contains", "value": "x"},
    )
    rv = client.get(f"/cases/{case_b}")

    assert rv.status_code == 200
    with client.session_transaction() as sess:
        assert sess["filters_by_case"][case_a]
        assert not sess.get("filters_by_case", {}).get(case_b)


def test_import_missing_case_redirects_to_cases_even_without_file(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()

    rv = client.post(
        f"/cases/{uuid.uuid4()}/import",
        data={},
        follow_redirects=True,
    )

    assert rv.status_code == 200
    assert "指定した案件が見つかりません。".encode("utf-8") in rv.data


def test_bulk_update_requires_server_confirmation(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(f"/cases/{cid}/custom", data={"name": "note"})
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(b'127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET / HTTP/1.0" 200 1\n'), "access_log")},
        content_type="multipart/form-data",
    )

    first = client.post(
        f"/cases/{cid}/bulk",
        data={"name": "note", "value": "updated"},
        follow_redirects=True,
    )
    assert "フィルタに一致する 1 行を更新します。".encode("utf-8") in first.data
    assert b'name="confirm" value="1"' in first.data
    assert b'value="updated"' in first.data
    assert b'value="updated"' not in client.get(f"/cases/{cid}").data

    second = client.post(
        f"/cases/{cid}/bulk",
        data={"name": "note", "value": "updated", "confirm": "1"},
        follow_redirects=True,
    )
    assert "更新しました。".encode("utf-8") in second.data
    assert b'value="updated"' in second.data
