from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from logcase.errors import AppError
from logcase.import_log import import_staged_file as write_parsed_import
from logcase.messages import MSG


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, AppError):
        message = exc.message
        cause = exc.__cause__
    else:
        message = MSG["E_WRK_SYS01"]
        cause = exc
    if cause is None:
        return message
    detail = str(cause).splitlines()[0].strip()
    if not detail or detail == message:
        return message
    return f"{message} {detail[:180]}"


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
    error: str = ""


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
    snapshot_custom_names: list = field(default_factory=list)
    snapshot_named: list = field(default_factory=list)


class ImportJobStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._jobs: dict[str, ImportJob] = {}
        self._claims: dict[str, set[str]] = {}

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
                        "error": item.error,
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
            "failed": any(item["status"] == "failed" for item in files),
            "files": files,
        }

    def _case_running_locked(self, case_id: str) -> bool:
        existing = self._jobs.get(case_id)
        return existing is not None and existing.state == "running"

    def enqueue(self, case_id: str, items: list[tuple[str, bytes]], snapshot: dict) -> ImportJob:
        with self._lock:
            if self._case_running_locked(case_id):
                raise ValueError("running")
            claim = uuid.uuid4().hex
            self._claims.setdefault(case_id, set()).add(claim)

        private = staging_root(self.data_dir) / ".enqueue_tmp" / f"{case_id}_{claim}"
        progress: list[FileProgress] = []
        try:
            private.mkdir(parents=True, exist_ok=True)
            for index, (name, data) in enumerate(items):
                path = private / f"{index}_{name}"
                path.write_bytes(data)
                progress.append(FileProgress(name=name, staged_path=str(path), status="queued"))
        except Exception:
            shutil.rmtree(private, ignore_errors=True)
            with self._lock:
                self._drop_claim_locked(case_id, claim)
            raise

        job: ImportJob | None = None
        try:
            with self._lock:
                self._drop_claim_locked(case_id, claim)
                if not self._case_running_locked(case_id):
                    final = staging_root(self.data_dir) / case_id
                    final.mkdir(parents=True, exist_ok=True)
                    for item in progress:
                        src = Path(item.staged_path)
                        dest = final / src.relative_to(private)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        src.replace(dest)
                        item.staged_path = str(dest)
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
                        snapshot_custom_names=list(snapshot.get("custom_names") or []),
                        snapshot_named=list(snapshot.get("named") or []),
                    )
                    self._jobs[case_id] = job
        finally:
            shutil.rmtree(private, ignore_errors=True)
        if job is None:
            raise ValueError("running")
        return job

    def _drop_claim_locked(self, case_id: str, claim: str) -> None:
        claims = self._claims.get(case_id)
        if claims is None:
            return
        claims.discard(claim)
        if not claims:
            self._claims.pop(case_id, None)

    def start(
        self,
        case_id: str,
        open_case_fn: Callable[[], Any],
        invalidate_fn: Callable[[str], None],
    ) -> None:
        thread: threading.Thread | None = None
        with self._lock:
            job = self._jobs.get(case_id)
            if job is None:
                raise KeyError(case_id)
            if job.thread is None:
                thread = threading.Thread(
                    target=self._run,
                    args=(case_id, open_case_fn, invalidate_fn),
                    daemon=True,
                )
                job.thread = thread
        if thread is not None:
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

    def _mark_remaining_failed(self, job: ImportJob | None) -> None:
        if job is None:
            return
        with self._lock:
            for item in job.files:
                if item.status not in {"done", "failed"}:
                    item.status = "failed"

    def _close_cleanup_and_finish(self, case_id: str, job: ImportJob | None, conn: Any) -> None:
        try:
            if conn is not None:
                conn.close()
        finally:
            if job is not None:
                with self._lock:
                    owned = self._jobs.get(case_id) is job
                if owned:
                    shutil.rmtree(staging_root(self.data_dir) / case_id, ignore_errors=True)
                    with self._lock:
                        if self._jobs.get(case_id) is job:
                            job.state = "finished"

    def _run(
        self,
        case_id: str,
        open_case_fn: Callable[[], Any],
        invalidate_fn: Callable[[str], None],
    ) -> None:
        with self._lock:
            job = self._jobs.get(case_id)
        conn = None
        try:
            try:
                conn = open_case_fn()
            except Exception:
                self._mark_remaining_failed(job)
                return
            if job is None:
                return
            items = list(job.files)
            for item in items:
                with self._lock:
                    item.status = "running"
                try:
                    result = write_parsed_import(conn, item.name, Path(item.staged_path))
                    with self._lock:
                        item.status = "done"
                        item.read_count = result.read_count
                        item.inserted_count = result.inserted_count
                        item.discarded_count = result.discarded_count
                        item.duplicate_count = result.duplicate_count
                        item.error = ""
                    invalidate_fn(case_id)
                except Exception as exc:
                    with self._lock:
                        item.status = "failed"
                        item.error = _error_text(exc)
        finally:
            self._close_cleanup_and_finish(case_id, job, conn)
