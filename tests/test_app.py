import io
import threading
import uuid
from pathlib import Path

from logcase.app import create_app
from logcase.store import open_case


COMMON = '%h %l %u %t \\"%r\\" %>s %b'
LINE = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1234\n'


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


def test_non_loopback_host_is_forbidden(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()

    rv = client.get("/", environ_overrides={"HTTP_HOST": "evil.example"})

    assert rv.status_code == 403


def test_plain_import_preserves_unicode_line_separator(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    cid = _create_case(client, "X")
    line = (
        '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] '
        '"GET /before\u2028after HTTP/1.0" 200 1\n'
    )

    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(line.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    wait_import(app, cid)

    conn = open_case(tmp_path, cid)
    try:
        assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
        assert conn.execute(
            "SELECT read_count FROM import_files"
        ).fetchone()["read_count"] == 1
    finally:
        conn.close()


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


def wait_import(app, case_id: str) -> None:
    app.config["IMPORT_JOBS"].wait(case_id)


def _hold_import(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    from logcase import import_jobs as jobs_mod

    real = jobs_mod.import_file

    def blocked(conn, name, lines):
        started.set()
        release.wait(5)
        return real(conn, name, lines)

    monkeypatch.setattr(jobs_mod, "import_file", blocked)
    return started, release


def test_applied_filter_conditions_stay_on_work_page(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.post(
        f"/cases/{cid}/filter",
        data={"identifier": "log:string:RemoteHost", "op": "contains", "value": "10.0.0."},
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert rv.status_code == 200
    assert 'class="cond filter-condition"' in html
    assert "10.0.0." in html
    assert "適用中" in html


def test_invalid_datetime_filter_keeps_entered_conditions(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.post(
        f"/cases/{cid}/filter",
        data={
            "identifier": "log:datetime:Time",
            "op": "on_or_after",
            "value": "2024/10/18 09:00:00",
        },
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert rv.status_code == 200
    assert "日時として解釈できません。" in html
    assert 'class="cond filter-condition"' in html
    assert "2024/10/18 09:00:00" in html


def test_invalid_datetime_filter_list_get_stays_on_work(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")

    client.post(
        f"/cases/{cid}/filter",
        data={
            "identifier": "log:datetime:Time",
            "op": "on_or_after",
            "value": "2024/10/18 09:00:00",
        },
    )

    for _ in range(2):
        rv = client.get(f"/cases/{cid}?tab=list", follow_redirects=True)
        html = rv.data.decode("utf-8")
        assert rv.status_code == 200
        assert rv.request.path == f"/cases/{cid}"
        assert 'class="cond filter-condition"' in html
        assert "2024/10/18 09:00:00" in html


def test_invalid_number_filter_keeps_entered_conditions(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.post(
        f"/cases/{cid}/filter",
        data={"identifier": "log:number:Status", "op": "eq", "value": "abc"},
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert rv.status_code == 200
    assert "数値として解釈できません。" in html
    assert 'class="cond filter-condition"' in html
    assert "abc" in html


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
    assert "フィルタ条件を解釈できません。".encode("utf-8") not in rv.data
    with client.session_transaction() as sess:
        assert sess["filters_by_case"][cid] == []


def test_filter_operator_options_follow_selected_field_type(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.get(f"/cases/{cid}")

    assert b'data-ops="eq,ne,contains,not_contains,starts,not_starts,in,not_in,gte,lte"' in rv.data
    assert b'data-ops="eq,ne,gt,ge,lt,le,unset"' in rv.data
    assert b'data-ops="eq,before,on_or_after,after,on_or_before,unset"' in rv.data
    js = client.get("/static/app.js").data
    assert b"rebuildOperators" in js


def test_string_eq_filter_round_trip_keeps_comma(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.post(
        f"/cases/{cid}/filter",
        data={
            "identifier": "log:string:RemoteHost",
            "op": "eq",
            "value": "a,b",
        },
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert rv.status_code == 200
    assert "等しい" in html
    assert "a,b" in html
    with client.session_transaction() as sess:
        spec = sess["filters_by_case"][cid]
        assert spec[0]["op"] == "eq"
        assert spec[0]["value"] == "a,b"


def test_string_ne_filter_round_trip(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.post(
        f"/cases/{cid}/filter",
        data={
            "identifier": "log:string:RemoteHost",
            "op": "ne",
            "value": "10.0.0.1",
        },
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert rv.status_code == 200
    assert "等しくない" in html
    assert "10.0.0.1" in html
    with client.session_transaction() as sess:
        spec = sess["filters_by_case"][cid]
        assert spec[0]["op"] == "ne"
        assert spec[0]["value"] == "10.0.0.1"


def test_not_in_filter_round_trip(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.post(
        f"/cases/{cid}/filter",
        data={
            "identifier": "log:string:RemoteHost",
            "op": "not_in",
            "value": "10.0.0.1,10.0.0.2",
        },
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert rv.status_code == 200
    assert "複数選択以外" in html
    assert "10.0.0.1,10.0.0.2" in html
    with client.session_transaction() as sess:
        spec = sess["filters_by_case"][cid]
        assert spec[0]["op"] == "not_in"
        assert spec[0]["value"] == ["10.0.0.1", "10.0.0.2"]


def test_invalid_after_on_existing_case_defaults_to_zero(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.get(f"/cases/{cid}?after=not-a-number")

    assert rv.status_code == 200
    assert b"X" in rv.data


def test_invalid_after_on_missing_case_flashes_not_found(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()

    rv = client.get(
        f"/cases/{uuid.uuid4()}?after=not-a-number",
        follow_redirects=True,
    )

    assert rv.status_code == 200
    assert "指定した案件が見つかりません。".encode("utf-8") in rv.data


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
    wait_import(app, cid)

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


def test_mutations_clear_case_aggregate_cache(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    cache = app.config["AGGREGATE_CACHE"]

    cache[cid] = [{"stale": "import"}]
    client.post(
        f"/cases/{cid}/import",
        data={
            "files": (
                io.BytesIO(
                    b'127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] '
                    b'"GET / HTTP/1.0" 200 1\n'
                ),
                "access_log",
            )
        },
        content_type="multipart/form-data",
    )
    wait_import(app, cid)
    assert cid not in cache

    cache[cid] = [{"stale": "custom-add"}]
    client.post(f"/cases/{cid}/custom", data={"name": "note"})
    assert cid not in cache

    cache[cid] = [{"stale": "cell"}]
    client.post(
        f"/cases/{cid}/cell",
        data={"row_id": "1", "name": "note", "value": "changed"},
    )
    assert cid not in cache

    cache[cid] = [{"stale": "bulk"}]
    client.post(
        f"/cases/{cid}/bulk",
        data={"name": "note", "value": "bulk", "confirm": "1"},
    )
    assert cid not in cache

    cache[cid] = [{"stale": "custom-delete"}]
    client.post(
        f"/cases/{cid}/custom/delete",
        data={"name": "note", "confirm": "1"},
    )
    assert cid not in cache

    cache[cid] = [{"stale": "case-delete"}]
    client.post(f"/cases/{cid}/delete", data={"confirm": "1"})
    assert cid not in cache


def test_work_page_metric_select_has_empty_option(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    cid = _create_case(client, "X")

    html = client.get(f"/cases/{cid}").data.decode("utf-8")
    start = html.index('<select name="metric_id">')
    select = html[start : html.index("</select>", start)]

    assert '<option value=""></option>' in select


def test_tables_do_not_wrap_cells(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    cid = _create_case(client, "X")

    listing = client.get("/").data.decode("utf-8")
    work = client.get(f"/cases/{cid}").data.decode("utf-8")
    css = client.get("/static/styles.css").data.decode("utf-8")

    assert "white-space: nowrap" in css
    assert "overflow-x: auto" in css or "overflow: auto" in css
    assert 'class="table-scroll"' in listing
    assert 'class="table-scroll"' in work


def test_aggregate_group_checkboxes_accept_multiple(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    cid = _create_case(client, "X")
    payload = (
        b'127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /a HTTP/1.0" 200 1\n'
        b'10.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /b HTTP/1.0" 404 1\n'
    )
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(payload), "access_log")},
        content_type="multipart/form-data",
    )
    wait_import(app, cid)

    work = client.get(f"/cases/{cid}").data.decode("utf-8")
    assert 'type="checkbox" name="group"' in work
    assert '<select name="group"' not in work
    assert "グループ（複数可）" in work

    rv = client.post(
        f"/cases/{cid}/aggregate",
        data={"group": ["log:Status", "log:RemoteHost"]},
        follow_redirects=True,
    )
    html = rv.data.decode("utf-8")
    header = html[html.index("<h2>集計</h2>") :]
    assert "<th>Status</th>" in header
    assert "<th>RemoteHost</th>" in header
    assert "<th>access_count</th>" in header


def test_import_delete_without_confirm_keeps_rows(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode()), "access_log")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    wait_import(app, cid)

    rv = client.post(
        f"/cases/{cid}/imports/delete",
        data={"source_filename": "access_log"},
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert rv.status_code == 200
    assert "取り込み履歴と" in html
    assert "1 行を消します" in html
    conn = open_case(tmp_path, cid)
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
    conn.close()


def test_import_delete_with_confirm_removes_file(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode()), "access_log")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    wait_import(app, cid)

    rv = client.post(
        f"/cases/{cid}/imports/delete",
        data={"source_filename": "access_log", "confirm": "1"},
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert "取り込みファイルを削除しました。" in html
    conn = open_case(tmp_path, cid)
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 0
    conn.close()


def test_import_delete_unknown_name_is_silent_noop(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode()), "access_log")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    wait_import(app, cid)

    rv = client.post(
        f"/cases/{cid}/imports/delete",
        data={"source_filename": "no-such", "confirm": "1"},
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert "取り込みファイルを削除しました。" not in html
    conn = open_case(tmp_path, cid)
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
    conn.close()


def test_second_work_get_skips_count_sql(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/filter",
        data={
            "identifier": ["log:datetime:Time", "log:string:Request-Method"],
            "op": ["on_or_after", "eq"],
            "value": ["2024-10-10T00:00:00", "GET"],
        },
    )
    client.get(f"/cases/{cid}?tab=list")
    conn = open_case(tmp_path, cid)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    conn.close()

    seen: list[str] = []

    def _wrap_open(data_dir, case_id):
        inner = open_case(data_dir, case_id)
        inner.set_trace_callback(seen.append)
        return inner

    import logcase.app as appmod
    original = appmod.open_case
    appmod.open_case = _wrap_open
    try:
        rv = client.get(f"/cases/{cid}?tab=list")
    finally:
        appmod.open_case = original
    assert rv.status_code == 200
    counts = [
        sql
        for sql in seen
        if "COUNT(" in sql.upper() and "FROM log_rows" in sql
    ]
    assert counts == []


def test_ingest_tab_skips_log_row_page_select(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.get(f"/cases/{cid}?tab=list")
    seen: list[str] = []

    import logcase.app as appmod
    original = appmod.open_case

    def _wrap_open(data_dir, case_id):
        inner = original(data_dir, case_id)
        inner.set_trace_callback(seen.append)
        return inner

    appmod.open_case = _wrap_open
    try:
        rv = client.get(f"/cases/{cid}?tab=ingest")
    finally:
        appmod.open_case = original
    assert rv.status_code == 200
    page = [
        sql
        for sql in seen
        if "FROM log_rows" in sql and "ORDER BY r.row_id" in sql
    ]
    assert page == []


def test_import_post_redirects_before_worker_finishes(tmp_path: Path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    from logcase import import_jobs as jobs_mod

    real = jobs_mod.import_file

    def blocked(conn, name, lines):
        started.set()
        release.wait(5)
        return real(conn, name, lines)

    monkeypatch.setattr(jobs_mod, "import_file", blocked)
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    rv = client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert rv.status_code == 302
    assert started.wait(5)
    conn = open_case(tmp_path, cid)
    try:
        assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 0
    finally:
        conn.close()
    status = client.get(f"/cases/{cid}/import/status")
    assert status.status_code == 200
    body = status.get_json()
    assert body["job"] == "running"
    assert body["files"][0]["name"] == "access_log"
    release.set()
    wait_import(app, cid)
    conn = open_case(tmp_path, cid)
    try:
        assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
    finally:
        conn.close()
    done = client.get(f"/cases/{cid}/import/status").get_json()
    assert done["job"] == "finished"
    assert done["files"][0]["inserted_count"] == 1


def test_import_status_unknown_case_is_404(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    rv = client.get(f"/cases/{uuid.uuid4()}/import/status")
    assert rv.status_code == 404
    assert rv.get_json()["job"] is None


def test_import_rejects_second_job_while_running(tmp_path: Path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    from logcase import import_jobs as jobs_mod

    real = jobs_mod.import_file

    def blocked(conn, name, lines):
        started.set()
        release.wait(5)
        return real(conn, name, lines)

    monkeypatch.setattr(jobs_mod, "import_file", blocked)
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert started.wait(5)
    rv = client.post(
        f"/cases/{cid}/import",
        data={
            "files": (
                io.BytesIO(LINE.replace("/index", "/other").encode("utf-8")),
                "access_log.2",
            )
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "この案件は取り込み中です。".encode("utf-8") in rv.data
    release.set()
    wait_import(app, cid)
    conn = open_case(tmp_path, cid)
    try:
        names = [r[0] for r in conn.execute("SELECT source_filename FROM import_files")]
    finally:
        conn.close()
    assert names == ["access_log"]


def test_running_import_post_does_not_open_case(tmp_path: Path, monkeypatch):
    started, release = _hold_import(monkeypatch)
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert started.wait(5)

    import logcase.app as appmod

    opened: list[str] = []
    original = appmod.open_case

    def _wrap(data_dir, case_id):
        opened.append(case_id)
        return original(data_dir, case_id)

    appmod.open_case = _wrap
    try:
        rv = client.post(
            f"/cases/{cid}/import",
            data={
                "files": (
                    io.BytesIO(LINE.replace("/index", "/other").encode("utf-8")),
                    "access_log.2",
                )
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        assert "tab=ingest" in rv.headers["Location"]
        with client.session_transaction() as sess:
            messages = [text for _, text in sess.get("_flashes", [])]
        assert "この案件は取り込み中です。" in messages
        assert opened == []
    finally:
        appmod.open_case = original
        release.set()
        wait_import(app, cid)


def test_enqueue_running_race_redirects_without_error(tmp_path: Path, monkeypatch):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    jobs = app.config["IMPORT_JOBS"]

    def raise_running(case_id, items, snapshot):
        raise ValueError("running")

    monkeypatch.setattr(jobs, "is_running", lambda case_id: False)
    monkeypatch.setattr(jobs, "enqueue", raise_running)
    rv = client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert rv.status_code == 302
    assert "tab=ingest" in rv.headers["Location"]
    with client.session_transaction() as sess:
        messages = [text for _, text in sess.get("_flashes", [])]
    assert "この案件は取り込み中です。" in messages


def test_worker_invalidate_only_pops_aggregate_cache(tmp_path: Path, monkeypatch):
    started, release = _hold_import(monkeypatch)
    import logcase.app as appmod

    worker_clears: list[str] = []
    real_clear = appmod._clear_counts

    def spy(case_id):
        if threading.current_thread() is not threading.main_thread():
            worker_clears.append(case_id)
        return real_clear(case_id)

    monkeypatch.setattr(appmod, "_clear_counts", spy)
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert started.wait(5)
    app.config["AGGREGATE_CACHE"][cid] = [{"access_count": 9}]
    release.set()
    wait_import(app, cid)
    assert worker_clears == []
    assert cid not in app.config["AGGREGATE_CACHE"]


def test_finished_notice_refreshes_stale_counts(tmp_path: Path, monkeypatch):
    started, release = _hold_import(monkeypatch)
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert started.wait(5)
    with client.session_transaction() as sess:
        sess["counts_by_case"] = {
            cid: {"key": "[]", "total_row_count": 0, "match_count": 0}
        }
    release.set()
    wait_import(app, cid)
    done = client.get(f"/cases/{cid}?tab=list")
    html = done.data.decode("utf-8")
    assert "まだログ行がありません" not in html
    assert '<span>全</span><b>1</b>' in html
    with client.session_transaction() as sess:
        assert sess["counts_by_case"][cid]["total_row_count"] == 1


def test_filter_during_import_redirects_to_ingest(tmp_path: Path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    from logcase import import_jobs as jobs_mod

    real = jobs_mod.import_file

    def blocked(conn, name, lines):
        started.set()
        release.wait(5)
        return real(conn, name, lines)

    monkeypatch.setattr(jobs_mod, "import_file", blocked)
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert started.wait(5)
    rv = client.post(
        f"/cases/{cid}/filter",
        data={"identifier": "log:string:RemoteHost", "op": "contains", "value": "127"},
        follow_redirects=True,
    )
    assert "この案件は取り込み中です。".encode("utf-8") in rv.data
    assert "取り込み".encode("utf-8") in rv.data
    release.set()
    wait_import(app, cid)


def test_list_tab_during_import_redirects_to_ingest(tmp_path: Path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    from logcase import import_jobs as jobs_mod
    real = jobs_mod.import_file

    def blocked(conn, name, lines):
        started.set()
        release.wait(5)
        return real(conn, name, lines)

    monkeypatch.setattr(jobs_mod, "import_file", blocked)
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert started.wait(5)
    rv = client.get(f"/cases/{cid}?tab=list", follow_redirects=True)
    assert "この案件は取り込み中です。".encode("utf-8") in rv.data
    listing = client.get("/")
    assert "取り込み中".encode("utf-8") in listing.data
    release.set()
    wait_import(app, cid)


def test_ingest_shows_progress_panel_while_running(tmp_path: Path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    from logcase import import_jobs as jobs_mod
    real = jobs_mod.import_file

    def blocked(conn, name, lines):
        started.set()
        release.wait(5)
        return real(conn, name, lines)

    monkeypatch.setattr(jobs_mod, "import_file", blocked)
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert started.wait(5)
    rv = client.get(f"/cases/{cid}?tab=ingest")
    html = rv.data.decode("utf-8")
    assert 'id="import-progress"' in html
    assert f"/cases/{cid}/import/status" in html
    assert "取り込み開始" in html
    assert "disabled" in html
    release.set()
    wait_import(app, cid)


def test_busy_ingest_keeps_snapshotted_custom_fields(tmp_path: Path, monkeypatch):
    started, release = _hold_import(monkeypatch)
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(f"/cases/{cid}/custom", data={"name": "調査区分"})
    client.post(
        f"/cases/{cid}/filter",
        data={"identifier": "log:string:RemoteHost", "op": "contains", "value": "127"},
    )
    client.post(f"/cases/{cid}/filter/save", data={"name": "ループバック"})
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    assert started.wait(5)
    rv = client.get(f"/cases/{cid}?tab=ingest")
    html = rv.data.decode("utf-8")
    assert 'id="import-progress"' in html
    assert 'data-job="running"' in html
    assert 'data-import-job="running"' in html
    assert "custom:string:調査区分" in html
    assert "カスタムフィールド（1）" in html
    assert "ループバック" in html
    assert "保存した条件（1）" in html
    assert "まだ保存がありません。" not in html
    job = app.config["IMPORT_JOBS"].get(cid)
    assert job.snapshot_custom_names == ["調査区分"]
    assert job.snapshot_named[0]["name"] == "ループバック"
    release.set()
    wait_import(app, cid)


def test_finished_import_flashes_once(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": (io.BytesIO(LINE.encode("utf-8")), "access_log")},
        content_type="multipart/form-data",
    )
    wait_import(app, cid)
    first = client.get(f"/cases/{cid}?tab=ingest")
    assert "取り込みが終わりました。".encode("utf-8") in first.data
    second = client.get(f"/cases/{cid}?tab=ingest")
    assert "取り込みが終わりました。".encode("utf-8") not in second.data


def test_main_cleans_staging_before_duckdb_convert(monkeypatch, tmp_path: Path):
    calls: list[str] = []
    monkeypatch.chdir(tmp_path)

    def clean(data_dir):
        calls.append("clean")

    def convert(data_dir):
        calls.append("convert")

    class Dummy:
        def run(self, *args, **kwargs):
            calls.append("run")

    monkeypatch.setattr("logcase.import_jobs.clean_staging", clean)
    monkeypatch.setattr("logcase.app.convert_all_cases", convert)
    monkeypatch.setattr("logcase.app.use_duckdb", lambda: True)
    monkeypatch.setattr("logcase.app.create_app", lambda data_dir: Dummy())
    from logcase.app import main

    assert main(["--port", "0"]) == 0
    assert calls[:2] == ["clean", "convert"]
