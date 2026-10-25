# 取り込み非同期化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 複数ファイルの取り込みを裏で進め、ブラウザは取り込みタブにすぐ戻ってファイルごとの進捗を見られるようにする。

**Architecture:** 受け付けたバイト列を `{data_dir}/.import_jobs/{case_id}/` に置き、プロセス内の `ImportJobStore` が 1 案件 1 スレッドで今と同じ `import_file` / `import_gzip_bytes` を順に呼ぶ。実行中はその案件の `open_case` を避け、status JSON はメモリだけを返す。完了後に取り込みタブを 1 回再読み込みする。

**Tech Stack:** Flask SSR、threading、既存の DuckDB / SQLite 接続、`fetch` ポーリング（2 秒）

## Global Constraints

- 解析エンジンと取り込みロジック（重複・破棄・派生列）は変えない
- 外部キュー、SSE、キャンセル、再起動後の再開はしない
- 1 案件につき実行中ジョブは 1 つ。他案件は同時に動かしてよい
- 実行中はその案件で `open_case` しない
- コミットはユーザーが指示したときだけ行う。各 Task 末尾の Commit はユーザーが「コミットして」と言うまでスキップする
- Windows では `.\.venv\Scripts\python.exe -m pytest ...` を使う。`&&` は使わない

---

## File map

- Create: `src/logcase/import_jobs.py` — ジョブ表、ステージ、ワーカー、status JSON、起動時クリーン
- Create: `tests/test_import_jobs.py` — ジョブ単体
- Modify: `src/logcase/app.py` — 受け付け即 303、status GET、実行中ガード、busy 時の取り込みタブ、通知フラッシュ
- Modify: `src/logcase/store.py` — `list_cases(..., skip_ids=)` で実行中案件を開かない
- Modify: `src/logcase/messages.py` — I_WRK_INF02 / I_WRK_INF03 / W_WRK_BIZ06
- Modify: `src/logcase/templates/partials/ingest_panel.html` — 進行パネル、開始ボタン無効
- Modify: `src/logcase/templates/cases.html` — 「取り込み中」表示
- Modify: `src/logcase/static/app.js` — 2 秒ポーリング、finished で reload
- Modify: `tests/test_app.py` — POST 後に `wait`、非同期・ロックのテスト追加
- Modify: `tests/test_store.py` — skip_ids があれば追加

---

### Task 1: ImportJobStore とワーカー

**Files:**
- Create: `src/logcase/import_jobs.py`
- Test: `tests/test_import_jobs.py`

**Interfaces:**
- Consumes: `logcase.import_log.import_file`, `import_gzip_bytes`; `ImportResult`
- Produces:
  - `FileProgress(name: str, staged_path: str, status: str, read_count: int = 0, inserted_count: int = 0, discarded_count: int = 0, duplicate_count: int = 0)`
  - `ImportJob(case_id: str, files: list[FileProgress], state: str, notified: bool = False, thread: threading.Thread | None = None, snapshot_name: str = "", snapshot_logformat: str = "", snapshot_logformat_head: str = "", snapshot_row_count: int = 0, snapshot_file_count: int = 0, snapshot_last_import: str | None = None, snapshot_imports: list = field(default_factory=list), snapshot_log_fields: list = field(default_factory=list))`
  - `class ImportJobStore`
  - `store.get(case_id: str) -> ImportJob | None`
  - `store.is_running(case_id: str) -> bool`
  - `store.running_ids() -> set[str]`
  - `store.status_payload(case_id: str) -> dict`
  - `store.enqueue(case_id: str, items: list[tuple[str, bytes]], snapshot: dict) -> ImportJob` — items は `(display_name, data)`。ディスクは `{index}_{basename}`
  - `store.start(case_id: str, open_case_fn: Callable[[], Any], invalidate_fn: Callable[[str], None]) -> None`
  - `store.wait(case_id: str | None = None, timeout: float = 30) -> None`
  - `store.take_finished_notice(case_id: str) -> bool`
  - `staging_root(data_dir: Path) -> Path` = `data_dir / ".import_jobs"`
  - `clean_staging(data_dir: Path) -> None`

`status_payload` は次の形。ジョブ無ければ `{"job": None, "done": 0, "total": 0, "current": None, "files": []}`。

```python
{
  "job": "running" | "finished" | None,
  "done": 0,
  "total": 0,
  "current": "access_log.4" | None,
  "files": [
    {
      "name": "access_log",
      "status": "queued" | "running" | "done" | "failed",
      "read_count": 0,
      "inserted_count": 0,
      "discarded_count": 0,
      "duplicate_count": 0,
    }
  ],
}
```

`snapshot` のキー: `name`, `logformat`, `logformat_head`, `row_count`, `file_count`, `last_import`, `imports`, `log_fields`。

- [ ] **Step 1: Write the failing tests**

`tests/test_import_jobs.py`:

```python
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
```

`dict(row)` が sqlite3.Row で動かない場合は `dict(zip(row.keys(), row))` に直す。テストの意図はスナップショットが空でも enqueue できること。

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_import_jobs.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'logcase.import_jobs'`

- [ ] **Step 3: Write implementation**

`src/logcase/import_jobs.py` を新規作成する。要点:

```python
from __future__ import annotations

import io
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from logcase.import_log import import_file, import_gzip_bytes


def staging_root(data_dir: Path) -> Path:
    return data_dir / ".import_jobs"


def clean_staging(data_dir: Path) -> None:
    root = staging_root(data_dir)
    if root.exists():
        shutil.rmtree(root)


@dataclass
class FileProgress:
    name: str
    staged_path: str
    status: str
    read_count: int = 0
    inserted_count: int = 0
    discarded_count: int = 0
    duplicate_count: int = 0


@dataclass
class ImportJob:
    case_id: str
    files: list[FileProgress]
    state: str
    notified: bool = False
    thread: threading.Thread | None = None
    snapshot_name: str = ""
    snapshot_logformat: str = ""
    snapshot_logformat_head: str = ""
    snapshot_row_count: int = 0
    snapshot_file_count: int = 0
    snapshot_last_import: str | None = None
    snapshot_imports: list = field(default_factory=list)
    snapshot_log_fields: list = field(default_factory=list)


class ImportJobStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._jobs: dict[str, ImportJob] = {}

    def get(self, case_id: str) -> ImportJob | None:
        with self._lock:
            return self._jobs.get(case_id)

    def is_running(self, case_id: str) -> bool:
        job = self.get(case_id)
        return job is not None and job.state == "running"

    def running_ids(self) -> set[str]:
        with self._lock:
            return {cid for cid, job in self._jobs.items() if job.state == "running"}

    def status_payload(self, case_id: str) -> dict:
        job = self.get(case_id)
        if job is None:
            return {"job": None, "done": 0, "total": 0, "current": None, "files": []}
        files = []
        current = None
        done = 0
        with self._lock:
            for item in job.files:
                files.append(
                    {
                        "name": item.name,
                        "status": item.status,
                        "read_count": item.read_count,
                        "inserted_count": item.inserted_count,
                        "discarded_count": item.discarded_count,
                        "duplicate_count": item.duplicate_count,
                    }
                )
                if item.status == "running":
                    current = item.name
                if item.status in {"done", "failed"}:
                    done += 1
            state = job.state
        return {
            "job": state,
            "done": done,
            "total": len(files),
            "current": current,
            "files": files,
        }

    def enqueue(self, case_id: str, items: list[tuple[str, bytes]], snapshot: dict) -> ImportJob:
        with self._lock:
            existing = self._jobs.get(case_id)
            if existing is not None and existing.state == "running":
                raise ValueError("running")
            dest = staging_root(self.data_dir) / case_id
            dest.mkdir(parents=True, exist_ok=True)
            progress: list[FileProgress] = []
            for index, (name, data) in enumerate(items):
                path = dest / f"{index}_{name}"
                path.write_bytes(data)
                progress.append(FileProgress(name=name, staged_path=str(path), status="queued"))
            job = ImportJob(
                case_id=case_id,
                files=progress,
                state="running",
                snapshot_name=snapshot.get("name") or "",
                snapshot_logformat=snapshot.get("logformat") or "",
                snapshot_logformat_head=snapshot.get("logformat_head") or "",
                snapshot_row_count=int(snapshot.get("row_count") or 0),
                snapshot_file_count=int(snapshot.get("file_count") or 0),
                snapshot_last_import=snapshot.get("last_import"),
                snapshot_imports=list(snapshot.get("imports") or []),
                snapshot_log_fields=list(snapshot.get("log_fields") or []),
            )
            self._jobs[case_id] = job
            return job

    def start(
        self,
        case_id: str,
        open_case_fn: Callable[[], Any],
        invalidate_fn: Callable[[str], None],
    ) -> None:
        job = self.get(case_id)
        if job is None:
            raise KeyError(case_id)
        thread = threading.Thread(
            target=self._run,
            args=(case_id, open_case_fn, invalidate_fn),
            daemon=True,
        )
        job.thread = thread
        thread.start()

    def wait(self, case_id: str | None = None, timeout: float = 30) -> None:
        threads: list[threading.Thread] = []
        with self._lock:
            jobs = [self._jobs[case_id]] if case_id else list(self._jobs.values())
            for job in jobs:
                if job.thread is not None:
                    threads.append(job.thread)
        for thread in threads:
            thread.join(timeout)

    def take_finished_notice(self, case_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(case_id)
            if job is None or job.state != "finished" or job.notified:
                return False
            job.notified = True
            return True

    def _run(
        self,
        case_id: str,
        open_case_fn: Callable[[], Any],
        invalidate_fn: Callable[[str], None],
    ) -> None:
        conn = open_case_fn()
        try:
            job = self.get(case_id)
            if job is None:
                return
            for item in job.files:
                with self._lock:
                    item.status = "running"
                try:
                    data = Path(item.staged_path).read_bytes()
                    if item.name.lower().endswith(".gz"):
                        result = import_gzip_bytes(conn, item.name, data)
                    else:
                        text = io.TextIOWrapper(
                            io.BytesIO(data),
                            encoding="utf-8",
                            errors="replace",
                            newline="",
                        )
                        result = import_file(conn, item.name, text)
                    with self._lock:
                        item.status = "done"
                        item.read_count = result.read_count
                        item.inserted_count = result.inserted_count
                        item.discarded_count = result.discarded_count
                        item.duplicate_count = result.duplicate_count
                    invalidate_fn(case_id)
                except Exception:
                    with self._lock:
                        item.status = "failed"
            with self._lock:
                job = self._jobs[case_id]
                job.state = "finished"
        finally:
            conn.close()
            dest = staging_root(self.data_dir) / case_id
            shutil.rmtree(dest, ignore_errors=True)
```

`enqueue` 中に同じ `case_id` の finished ジョブがあっても上書きしてよい。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_import_jobs.py -v`

Expected: PASS（5 tests）

- [ ] **Step 5: Commit** （ユーザー指示があるまでスキップ）

```bash
git add src/logcase/import_jobs.py tests/test_import_jobs.py
git commit -m "feat: run case imports on a background job store"
```

---

### Task 2: POST 即返しと status、既存テストの wait

**Files:**
- Modify: `src/logcase/messages.py`
- Modify: `src/logcase/app.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: `ImportJobStore`, `clean_staging`（起動時は Task 3）, Task 1 のメソッド
- Produces:
  - `create_app` が `app.config["IMPORT_JOBS"] = ImportJobStore(root)` をセット
  - `POST /cases/<case_id>/import` はジョブ開始後すぐ 303
  - `GET /cases/<case_id>/import/status` は JSON。案件 ID 不正は 404 `{"job": null}`
  - テストヘルパ `wait_import(app, case_id)` = `app.config["IMPORT_JOBS"].wait(case_id)`

- [ ] **Step 1: Write the failing HTTP tests**

`tests/test_app.py` に追加。既存の取り込み POST の直後（DB アサートの前）に `wait_import(app, cid)` を入れる。対象:

- `test_plain_import_preserves_unicode_line_separator`
- `test_bulk_update_requires_server_confirmation`
- `test_mutations_clear_case_aggregate_cache`（import の直後。enqueue 時にもキャッシュを捨てること）
- `test_import_delete_*` 3 件（import POST のあと）

ヘルパ:

```python
def wait_import(app, case_id: str) -> None:
    app.config["IMPORT_JOBS"].wait(case_id)
```

新規:

```python
import threading

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
        data={"files": (io.BytesIO(LINE.replace(b"/index", b"/other")), "access_log.2")},
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
```

`LINE` が test_app 先頭に既にある。`test_plain_import...` は wait を足す:

```python
    client.post(...)
    wait_import(app, cid)
    conn = open_case(tmp_path, cid)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py::test_import_post_redirects_before_worker_finishes tests/test_app.py::test_import_status_unknown_case_is_404 tests/test_app.py::test_import_rejects_second_job_while_running -v`

Expected: FAIL（404 が無い、POST が同期完了して行が先に入る、など）

- [ ] **Step 3: Implement routes and messages**

`messages.py` に追加:

```python
    "I_WRK_INF02": "取り込みを開始しました。",
    "I_WRK_INF03": "取り込みが終わりました。",
    "W_WRK_BIZ06": "この案件は取り込み中です。",
```

`create_app` の `AGGREGATE_CACHE` の直後:

```python
    from logcase.import_jobs import ImportJobStore

    app.config["IMPORT_JOBS"] = ImportJobStore(root)
```

`work_import` を置き換える。拡張子・同名・空ファイルの扱いは今と同じ。残ったファイルを `enqueue` し、キャッシュを捨て、`start` する。`open_case_fn` は `lambda: open_case(root, case_id)`。`invalidate_fn` は `AGGREGATE_CACHE.pop` と `_clear_counts`。

```python
        jobs: ImportJobStore = app.config["IMPORT_JOBS"]
        if jobs.is_running(case_id):
            conn.close()
            _flash_code("W_WRK_BIZ06")
            return _work_redirect(case_id, "ingest")
        files = request.files.getlist("files")
        if not files or all(not item.filename for item in files):
            conn.close()
            _flash_code("E_WRK_REQ01")
            return _work_redirect(case_id, "ingest")
        existing = {
            row[0] for row in conn.execute("SELECT source_filename FROM import_files")
        }
        confirm = request.form.get("confirm_same_name") == "1"
        accepted: list[tuple[str, bytes]] = []
        for uploaded in files:
            name = Path(uploaded.filename or "").name
            if not _allowed_filename(name):
                _flash_code("E_WRK_FMT01")
                continue
            if name in existing and not confirm:
                _flash_code("W_WRK_BIZ01")
                continue
            accepted.append((name, uploaded.read()))
        if not accepted:
            conn.close()
            return _work_redirect(case_id, "ingest")
        meta = conn.execute("SELECT name, logformat FROM meta").fetchone()
        imports = conn.execute(
            "SELECT source_filename, read_count, inserted_count, "
            "discarded_count, duplicate_count, imported_at "
            "FROM import_files ORDER BY id"
        ).fetchall()
        log_fields = conn.execute(
            "SELECT identifier, field_type, specifier, time_axis "
            "FROM log_fields ORDER BY display_order"
        ).fetchall()
        snapshot = {
            "name": meta["name"],
            "logformat": meta["logformat"],
            "logformat_head": (meta["logformat"] or "")[:80],
            "row_count": conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0],
            "file_count": len(imports),
            "last_import": max((row["imported_at"] for row in imports), default=None),
            "imports": [dict(zip(row.keys(), row)) for row in imports],
            "log_fields": [dict(zip(row.keys(), row)) for row in log_fields],
        }
        conn.close()
        jobs.enqueue(case_id, accepted, snapshot)
        app.config["AGGREGATE_CACHE"].pop(case_id, None)
        _clear_counts(case_id)
        jobs.start(
            case_id,
            lambda: open_case(root, case_id),
            lambda cid: (
                app.config["AGGREGATE_CACHE"].pop(cid, None),
                _clear_counts(cid),
            )[-1] or None,
        )
        _flash_code("I_WRK_INF02")
        return _work_redirect(case_id, "ingest")
```

`invalidate_fn` は副作用関数にする（タプル技ではなく）:

```python
        def invalidate(cid: str) -> None:
            app.config["AGGREGATE_CACHE"].pop(cid, None)
            _clear_counts(cid)
```

`work_import` の `try` の `finally: conn.close()` は、スナップショット後に自分で閉じるので二重 close しない。例外時だけ閉じる。

status ルート（`work_import` の直後）:

```python
    @app.get("/cases/<case_id>/import/status")
    def work_import_status(case_id: str):
        from logcase.store import _public_case_id

        if _public_case_id(case_id) is None:
            return jsonify({"job": None}), 404
        if not (root / f"{case_id}.sqlite").is_file():
            return jsonify({"job": None}), 404
        payload = app.config["IMPORT_JOBS"].status_payload(case_id)
        return jsonify(payload)
```

`_public_case_id` を store から export して使う。今は store のプライベート関数なので、status では `open_case` せず `Path.is_file()` と UUID 形式だけ見る。`_public_case_id` を `store.py` で公開するか、status 内で UUID 判定を複製する。公開する:

```python
# store.py の _public_case_id はそのまま。app からは
from logcase.store import case_path
if _public_case_id is private, add:

def case_exists(data_dir: Path, case_id: str) -> bool:
    return _public_case_id(case_id) is not None and case_path(data_dir, case_id).is_file()
```

`src/logcase/store.py` に `case_exists` を足し、status はそれだけで 404 を決める。DB は開かない。

- [ ] **Step 4: Run app import tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py tests/test_import_jobs.py -q`

Expected: PASS

- [ ] **Step 5: Commit** （ユーザー指示があるまでスキップ）

```bash
git add src/logcase/app.py src/logcase/messages.py src/logcase/store.py tests/test_app.py
git commit -m "feat: accept log imports without blocking the browser"
```

---

### Task 3: 実行中ガードと案件一覧

**Files:**
- Modify: `src/logcase/store.py` — `list_cases`
- Modify: `src/logcase/app.py` — ガード、busy コンテキスト、起動時クリーン、I_WRK_INF03
- Modify: `src/logcase/templates/cases.html`
- Test: `tests/test_app.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `ImportJobStore.is_running`, `running_ids`, `get`, `take_finished_notice`
- Produces:
  - `list_cases(data_dir, skip_ids: set[str] | None = None)`
  - 実行中のその案件への書き込み・一覧・集計・CSV・削除は W_WRK_BIZ06 で取り込みタブへ
  - 取り込みタブ GET は DB を開かずスナップショットで描画
  - `main` が `clean_staging(data_dir)` を呼ぶ（DuckDB 変換の前）

- [ ] **Step 1: Write the failing tests**

```python
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
```

`tests/test_store.py` に `skip_ids` を足すなら、実行中 ID のファイルを開かず `skipped: True` を返すテストを 1 つ。

- [ ] **Step 2: Run to verify fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py::test_filter_during_import_redirects_to_ingest tests/test_app.py::test_list_tab_during_import_redirects_to_ingest tests/test_app.py::test_finished_import_flashes_once -v`

Expected: FAIL

- [ ] **Step 3: Implement guards**

`list_cases` のシグネチャを `skip_ids: set[str] | None = None` にする。`path.stem in skip_ids` なら `open` せず:

```python
            rows.append(
                {
                    "id": path.stem,
                    "skipped": True,
                    "error": None,
                }
            )
            continue
```

`cases_view`:

```python
        jobs = app.config["IMPORT_JOBS"]
        cases = list_cases(root, skip_ids=jobs.running_ids())
        for case in cases:
            if not case.get("skipped"):
                continue
            job = jobs.get(case["id"])
            case.update(
                {
                    "name": job.snapshot_name if job else case["id"],
                    "logformat": job.snapshot_logformat if job else "",
                    "logformat_head": job.snapshot_logformat_head if job else "",
                    "file_count": job.snapshot_file_count if job else 0,
                    "row_count": job.snapshot_row_count if job else 0,
                    "last_import": job.snapshot_last_import if job else None,
                    "error": None,
                    "importing": True,
                    "skipped": False,
                }
            )
        else:
            case.setdefault("importing", False)
```

`for/else` は使わず、スキップしない案件に `importing=False` をセットする。

`cases.html` の案件名の横:

```html
                  <a class="case-name" href="{{ url_for('work_view', case_id=case.id) }}">{{ case.name }}</a>
                  {% if case.importing %}<span class="r" style="margin-left:8px">取り込み中</span>{% endif %}
```

`app.py` に:

```python
    def _busy_redirect(case_id: str):
        if app.config["IMPORT_JOBS"].is_running(case_id):
            _flash_code("W_WRK_BIZ06")
            return _work_redirect(case_id, "ingest")
        return None
```

次のルートの冒頭で `blocked = _busy_redirect(case_id); if blocked: return blocked`:

- `work_more`
- `work_import_delete`
- `work_filter` / save / load
- `work_custom` / delete / cell / bulk
- `work_aggregate`
- export rows / aggregate
- `cases_delete` / `cases_rename`（その case_id）

`_render_work`:

```python
        jobs = app.config["IMPORT_JOBS"]
        if jobs.take_finished_notice(case_id):
            _flash_code("I_WRK_INF03")
        tab = _requested_tab()
        if jobs.is_running(case_id) and tab != "ingest":
            _flash_code("W_WRK_BIZ06")
            return _work_redirect(case_id, "ingest")
        if jobs.is_running(case_id):
            return render_template("work.html", **_work_context_busy(case_id))
        return render_template("work.html", **_work_context(case_id, after, tab))
```

`_work_context_busy` は `open_case` しない。`job.snapshot_*` と session のフィルタから、`_work_context` と同じキーを埋める。`rows=[]`, `match_count=job.snapshot_row_count`, `total_row_count=job.snapshot_row_count`, `imports=job.snapshot_imports`, `tab="ingest"`, `import_job=jobs.status_payload(case_id)`。

通常の `_work_context` にも `import_job=jobs.status_payload(case_id)` を足す（finished パネル用）。

`main`:

```python
    from logcase.import_jobs import clean_staging

    clean_staging(data_dir)
    if use_duckdb():
        convert_all_cases(data_dir)
```

- [ ] **Step 4: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py tests/test_store.py tests/test_import_jobs.py -q`

Expected: PASS

- [ ] **Step 5: Commit** （ユーザー指示があるまでスキップ）

```bash
git add src/logcase/app.py src/logcase/store.py src/logcase/templates/cases.html tests/test_app.py tests/test_store.py
git commit -m "feat: keep a case read-only while an import job runs"
```

---

### Task 4: 取り込みタブの進捗 UI

**Files:**
- Modify: `src/logcase/templates/partials/ingest_panel.html`
- Modify: `src/logcase/static/app.js`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `import_job` コンテキスト（Task 3 の `status_payload`）
- Produces: 進行パネル、開始ボタン無効、2 秒 `fetch`、`job === "finished"` で `location.reload()`

- [ ] **Step 1: Write the failing UI test**

```python
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
```

- [ ] **Step 2: Run to verify fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py::test_ingest_shows_progress_panel_while_running -v`

Expected: FAIL with `id="import-progress"` not found

- [ ] **Step 3: Implement panel and JS**

`ingest_panel.html` のドロップフォームの直後（説明文の前）に:

```html
        {% if import_job and import_job.job %}
        <div id="import-progress"
             data-status-url="{{ url_for('work_import_status', case_id=case_id) }}"
             data-job="{{ import_job.job }}"
             style="margin-top:12px;border:1px solid var(--line);border-radius:3px;padding:10px 12px">
          <div style="font-size:12px;margin-bottom:8px">
            {{ import_job.done }} / {{ import_job.total }}
            {% if import_job.job == "running" %}
              処理中{% if import_job.current %}: {{ import_job.current }}{% endif %}
            {% else %}
              完了
            {% endif %}
          </div>
          <ul style="margin:0;padding-left:16px;font-size:11.5px;line-height:1.7">
            {% for f in import_job.files %}
              <li>
                <span class="mono">{{ f.name }}</span>
                — {{ f.status }}
                {% if f.status == "done" %}
                  （読込 {{ f.read_count }} / 登録 {{ f.inserted_count }} / 破棄 {{ f.discarded_count }} / 重複 {{ f.duplicate_count }}）
                {% endif %}
              </li>
            {% endfor %}
          </ul>
        </div>
        {% endif %}
```

開始ボタン:

```html
            <button type="submit" class="btn primary sm"{% if import_job and import_job.job == "running" %} disabled{% endif %}>{{ icon("play", 12) }}取り込み開始</button>
```

前提リストの「進行状況はこの画面に出ます。」はそのまま。

`app.js` の末尾の init 群に追加:

```javascript
function initImportProgress() {
  const box = document.getElementById("import-progress");
  if (!box) return;
  const url = box.getAttribute("data-status-url");
  if (!url || box.getAttribute("data-job") !== "running") return;
  const timer = setInterval(function () {
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data || data.job === "finished") {
          clearInterval(timer);
          window.location.reload();
        }
      })
      .catch(function () {});
  }, 2000);
}
```

`app.js` の `DOMContentLoaded`（342–352 行）に `initImportProgress();` を足す。既存の並びは `initFilter` / `initOverlays` / `initMenus` / `initTabs` / `initCaseRows` / `initCells` / `initToasts` / `initCreatePreview` / `initBucketLabel`。

- [ ] **Step 4: Run full tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: 全件 PASS

手動: Docker 再ビルド後、複数ファイルを取り込み、ブラウザがすぐ戻ること、進行が出ること、完了後に履歴が増えること、取り込み中に一覧タブへ行けないこと。

- [ ] **Step 5: Commit** （ユーザー指示があるまでスキップ）

```bash
git add src/logcase/templates/partials/ingest_panel.html src/logcase/static/app.js tests/test_app.py
git commit -m "feat: poll import progress on the ingest tab"
```

---

## Spec coverage

| Spec | Task |
| ---- | ---- |
| 1 目的（すぐ戻る・進捗） | 2, 4 |
| 2 裏スレッド + 既存 import_file | 1 |
| 3 1 案件 1 ジョブ、再取り込み拒否 | 1, 2 |
| 4 ステージ・連番名・起動時クリーン | 1, 3 |
| 5 ワーカー接続・失敗継続・キャッシュ破棄 | 1, 2 |
| 5 実行中は open_case しない / 操作停止 | 3 |
| 5 案件一覧は実行中を開かない | 3 |
| 6 status JSON・2 秒・reload・I_WRK_INF03 1 回 | 2, 3, 4 |
| 7 再起動で再開しない | 1 `clean_staging`, 3 `main` |
| 8 メッセージ 3 件 | 2, 3 |
| 9 テスト一覧 | 1–4 |
| 10 対象外 | 実装しない |
