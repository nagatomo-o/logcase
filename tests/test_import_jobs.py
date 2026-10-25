import gzip
from pathlib import Path

from logcase.import_jobs import ImportJobStore, clean_staging, staging_root
from logcase.store import create_case, open_case

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
LINE = b'127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 10\n'


def _snapshot(conn, name="t"):
    imports = [
        dict(row)
        if hasattr(row, "keys")
        else {}
        for row in conn.execute(
            "SELECT source_filename, read_count, inserted_count, "
            "discarded_count, duplicate_count, imported_at FROM import_files"
        )
    ]
    return {
        "name": name,
        "logformat": COMMON,
        "logformat_head": COMMON[:80],
        "row_count": conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0],
        "file_count": conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0],
        "last_import": None,
        "imports": imports,
        "log_fields": [],
    }


def test_enqueue_writes_staged_files_without_importing(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    store = ImportJobStore(tmp_path)
    job = store.enqueue(cid, [("access_log", LINE), ("access_log.2", LINE)], _snapshot(conn))
    conn.close()
    assert job.state == "running"
    assert [f.name for f in job.files] == ["access_log", "access_log.2"]
    assert all(Path(f.staged_path).is_file() for f in job.files)
    assert Path(job.files[0].staged_path).name.startswith("0_")
    conn = open_case(tmp_path, cid)
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 0
    conn.close()


def test_start_imports_files_and_status_reaches_finished(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    snap = _snapshot(conn)
    conn.close()
    store = ImportJobStore(tmp_path)
    store.enqueue(cid, [("access_log", LINE), ("n.gz", gzip.compress(LINE))], snap)
    store.start(cid, lambda: open_case(tmp_path, cid), lambda _cid: None)
    store.wait(cid)
    payload = store.status_payload(cid)
    assert payload["job"] == "finished"
    assert payload["done"] == 2
    assert payload["files"][0]["status"] == "done"
    assert payload["files"][0]["inserted_count"] == 1
    conn = open_case(tmp_path, cid)
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
    conn.close()
    assert store.take_finished_notice(cid) is True
    assert store.take_finished_notice(cid) is False


def test_second_enqueue_while_running_is_rejected(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    store = ImportJobStore(tmp_path)
    conn = open_case(tmp_path, cid)
    snap = _snapshot(conn)
    conn.close()
    store.enqueue(cid, [("access_log", LINE)], snap)
    try:
        store.enqueue(cid, [("other", LINE)], snap)
        raise AssertionError("should reject")
    except ValueError as exc:
        assert "running" in str(exc)


def test_failed_file_does_not_stop_later_files(tmp_path: Path, monkeypatch):
    from logcase import import_jobs as mod

    calls = {"n": 0}
    real = mod.import_file

    def flaky(conn, name, lines):
        calls["n"] += 1
        if name == "bad":
            raise RuntimeError("boom")
        return real(conn, name, lines)

    monkeypatch.setattr(mod, "import_file", flaky)
    cid = create_case(tmp_path, "t", COMMON)
    store = ImportJobStore(tmp_path)
    conn = open_case(tmp_path, cid)
    snap = _snapshot(conn)
    conn.close()
    store.enqueue(cid, [("bad", LINE), ("access_log", LINE)], snap)
    store.start(cid, lambda: open_case(tmp_path, cid), lambda _cid: None)
    store.wait(cid)
    payload = store.status_payload(cid)
    assert payload["files"][0]["status"] == "failed"
    assert payload["files"][1]["status"] == "done"
    conn = open_case(tmp_path, cid)
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
    conn.close()


def test_clean_staging_removes_job_dir(tmp_path: Path):
    root = staging_root(tmp_path)
    (root / "x").mkdir(parents=True)
    (root / "x" / "f").write_bytes(b"a")
    clean_staging(tmp_path)
    assert not root.exists()
