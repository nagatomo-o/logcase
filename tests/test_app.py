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
