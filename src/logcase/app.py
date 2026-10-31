from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from logcase.aggregate import Metric, aggregate
from logcase.csv_export import aggregate_csv_bytes, rows_csv_bytes
from logcase.custom_fields import (
    add_custom_field,
    bulk_set_custom,
    delete_custom_field,
    set_custom_value,
)
from logcase.errors import AppError, LogFormatError
from logcase.filters import Condition, count_rows, fetch_rows, normalize_sort
from logcase.import_log import delete_imported_file
from logcase.logformat import compile_logformat, parse_line
from logcase.messages import MSG, msg
from logcase.named_aggregates import (
    delete_named_aggregate,
    list_named_aggregates,
    load_named_aggregate,
    save_named_aggregate,
)
from logcase.named_filters import (
    conditions_from_spec,
    delete_named_filter,
    load_named_filter,
    save_named_filter,
)
from logcase.duckdb_store import convert_all_cases, use_duckdb
from logcase.store import (
    case_exists,
    create_case,
    delete_case,
    list_cases,
    open_case,
    rename_case,
)

OP_LABELS = {
    "contains": "含む",
    "not_contains": "含まない",
    "starts": "始まる",
    "not_starts": "始まらない",
    "in": "複数選択",
    "not_in": "複数選択以外",
    "gte": "以上",
    "lte": "以下",
    "unset": "未設定",
    "eq": "等しい",
    "ne": "等しくない",
    "gt": "より大きい",
    "ge": "以上(数値)",
    "lt": "より小さい",
    "le": "以下(数値)",
    "before": "より前",
    "on_or_after": "以降",
    "after": "より後",
    "on_or_before": "以前",
}

TABS = {"list", "agg", "ingest", "schema"}
ALLOWED_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0"})
LOOPBACK_REQUEST_HOSTS = frozenset({"127.0.0.1", "localhost"})


def listen_host(raw: str | None = None) -> str:
    if raw is None:
        raw = os.environ.get("ACCESS_LOG_BIND", "127.0.0.1")
    value = str(raw).strip()
    if value not in ALLOWED_BIND_HOSTS:
        return "127.0.0.1"
    return value


def extra_allowed_hosts(raw: str | None = None) -> frozenset[str]:
    if raw is None:
        raw = os.environ.get("ACCESS_LOG_HOST", "")
    return frozenset(
        part.strip().lower() for part in str(raw).split(",") if part.strip()
    )


def _allowed_filename(name: str) -> bool:
    return bool(name)


def _flash_code(code: str, **kwargs: object) -> None:
    text = msg(code, **kwargs) if kwargs else MSG[code]
    flash(text, {"S_": "ok", "W_": "warn", "E_": "err", "I_": "info"}.get(code[:2], "info"))


def _flash_err(exc: AppError) -> None:
    flash(exc.message, {"S_": "ok", "W_": "warn", "E_": "err", "I_": "info"}.get(exc.code[:2], "err"))


def _case_specs(case_id: str) -> list[dict]:
    return (session.get("filters_by_case") or {}).get(case_id) or []


def _filter_specs(case_id: str) -> list[dict]:
    filter_specs = []
    for item in _case_specs(case_id):
        try:
            value = item.get("value")
            if isinstance(value, list):
                value_text = ",".join(str(part) for part in value)
            else:
                value_text = "" if value is None else str(value)
            filter_specs.append(
                {
                    "identifier": item["identifier"],
                    "source": item["source"],
                    "field_type": item["field_type"],
                    "op": item["op"],
                    "encoded": f"{item['source']}:{item['field_type']}:{item['identifier']}",
                    "value_text": value_text,
                }
            )
        except (KeyError, TypeError):
            continue
    return filter_specs


def _session_conditions(case_id: str) -> list[Condition]:
    valid_specs = []
    for item in _case_specs(case_id):
        try:
            if item["source"] not in {"log", "custom"} or not _valid_filter_op(
                item["field_type"], item["op"]
            ):
                continue
            valid_specs.append(item)
        except (KeyError, TypeError):
            continue
    return conditions_from_spec(valid_specs)


def _set_case_filters(case_id: str, spec: list[dict]) -> None:
    filters = dict(session.get("filters_by_case") or {})
    filters[case_id] = spec
    session["filters_by_case"] = filters


def _set_named(case_id: str, name: str | None) -> None:
    data = dict(session.get("named_by_case") or {})
    if name:
        data[case_id] = name
    else:
        data.pop(case_id, None)
    session["named_by_case"] = data


def _set_agg_form(case_id: str, payload: dict) -> None:
    data = dict(session.get("agg_by_case") or {})
    data[case_id] = payload
    session["agg_by_case"] = data


def _set_named_agg(case_id: str, name: str | None) -> None:
    data = dict(session.get("named_agg_by_case") or {})
    if name:
        data[case_id] = name
    else:
        data.pop(case_id, None)
    session["named_agg_by_case"] = data


def _session_sort(case_id: str) -> str:
    data = session.get("sort_by_case") or {}
    return normalize_sort(data.get(case_id))


def _set_case_sort(case_id: str, sort: str) -> None:
    data = dict(session.get("sort_by_case") or {})
    data[case_id] = normalize_sort(sort)
    session["sort_by_case"] = data


def _effective_sort(case_id: str, has_time_axis: bool) -> str:
    if not has_time_axis:
        return "row_id"
    return _session_sort(case_id)


def _agg_payload_from_mapping(data: dict) -> dict:
    groups = data.get("groups") or []
    if not isinstance(groups, list):
        groups = []
    return {
        "groups": [str(item) for item in groups],
        "use_bucket": bool(data.get("use_bucket")),
        "n": str(data.get("n") or "1"),
        "unit": str(data.get("unit") or "hour"),
        "metric_id": str(data.get("metric_id") or ""),
        "metric_fn": str(data.get("metric_fn") or "count"),
    }


def _agg_payload_from_request() -> dict:
    return _agg_payload_from_mapping(
        {
            "groups": request.form.getlist("group"),
            "use_bucket": bool(request.form.get("use_bucket")),
            "n": request.form.get("n") or "1",
            "unit": request.form.get("unit") or "hour",
            "metric_id": request.form.get("metric_id") or "",
            "metric_fn": request.form.get("metric_fn") or "count",
        }
    )


def _filter_cache_key(case_id: str) -> str:
    return json.dumps(_case_specs(case_id), ensure_ascii=False, sort_keys=True)


def _get_counts(case_id: str) -> dict | None:
    return (session.get("counts_by_case") or {}).get(case_id)


def _set_counts(case_id: str, total: int, match: int) -> None:
    data = dict(session.get("counts_by_case") or {})
    data[case_id] = {
        "key": _filter_cache_key(case_id),
        "total_row_count": total,
        "match_count": match,
    }
    session["counts_by_case"] = data


def _clear_counts(case_id: str) -> None:
    if not has_request_context():
        return
    data = dict(session.get("counts_by_case") or {})
    data.pop(case_id, None)
    session["counts_by_case"] = data


def _invalidate_case(case_id: str) -> None:
    _clear_counts(case_id)


def _requested_tab() -> str:
    raw = request.form.get("tab") or request.args.get("tab") or "list"
    return raw if raw in TABS else "list"


def _work_redirect(case_id: str, tab: str | None = None):
    return redirect(url_for("work_view", case_id=case_id, tab=tab or _requested_tab()))


def _valid_filter_op(field_type: str, operator: str) -> bool:
    allowed = {
        "string": {"eq", "ne", "contains", "not_contains", "starts", "not_starts", "in", "not_in", "gte", "lte"},
        "number": {"eq", "ne", "gt", "ge", "lt", "le", "unset"},
        "datetime": {"eq", "before", "on_or_after", "after", "on_or_before", "unset"},
    }
    return operator in allowed.get(field_type, set())


def create_app(data_dir: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)
    root = Path(data_dir) if data_dir is not None else Path.cwd() / "data"
    root.mkdir(parents=True, exist_ok=True)
    app.config["DATA_DIR"] = root
    app.config.setdefault("AGGREGATE_CACHE", {})
    from logcase.import_jobs import ImportJobStore

    app.config["IMPORT_JOBS"] = ImportJobStore(root)
    app.config["ALLOWED_REQUEST_HOSTS"] = LOOPBACK_REQUEST_HOSTS | extra_allowed_hosts()

    @app.template_filter("comma")
    def comma_filter(value: object) -> str:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return "" if value is None else str(value)

    @app.template_filter("when")
    def when_filter(value: object) -> str:
        if not value:
            return "—"
        text = str(value).replace("T", " ")
        return text[:19] if len(text) >= 16 else text

    @app.before_request
    def require_loopback_host():
        hostname, separator, port = request.host.lower().partition(":")
        if hostname not in app.config["ALLOWED_REQUEST_HOSTS"] or (
            separator and not port.isdigit()
        ):
            abort(403)

    def _busy_redirect(case_id: str):
        if app.config["IMPORT_JOBS"].is_running(case_id):
            _flash_code("W_WRK_BIZ06")
            return _work_redirect(case_id, "ingest")
        return None

    @app.get("/")
    def cases_view():
        jobs = app.config["IMPORT_JOBS"]
        cases = list_cases(root, skip_ids=jobs.running_ids())
        for case in cases:
            if case.get("skipped"):
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
        ok = [c for c in cases if not c.get("error")]
        return render_template(
            "cases.html",
            cases=cases,
            case_count=len(ok),
            file_total=sum(c.get("file_count") or 0 for c in ok),
            row_total=sum(c.get("row_count") or 0 for c in ok),
            warn_delete=MSG["W_CSE_BIZ01"],
        )

    @app.post("/cases")
    def cases_create():
        try:
            create_case(root, request.form.get("name", ""), request.form.get("logformat", ""))
            _flash_code("S_CSE_OK01")
        except AppError as exc:
            _flash_err(exc)
        return redirect(url_for("cases_view"))

    @app.post("/cases/preview-format")
    def preview_format():
        try:
            compiled = compile_logformat(request.form.get("logformat", ""))
        except LogFormatError as exc:
            return jsonify({"ok": False, "message": exc.message, "code": exc.code})
        return jsonify(
            {
                "ok": True,
                "regex": compiled.pattern.pattern,
                "fields": [
                    {
                        "identifier": field.identifier,
                        "field_type": field.field_type,
                        "specifier": field.specifier,
                        "time_axis": field.time_axis,
                    }
                    for field in compiled.fields
                ],
            }
        )

    @app.post("/cases/preview-line")
    def preview_line():
        try:
            compiled = compile_logformat(request.form.get("logformat", ""))
        except LogFormatError as exc:
            return jsonify({"ok": False, "matched": False, "message": exc.message})
        parsed = parse_line(compiled, request.form.get("line", ""))
        if parsed is None:
            return jsonify({"ok": True, "matched": False})
        values = []
        for item in parsed.values:
            if item.num_value is not None:
                shown = str(item.num_value)
            else:
                shown = item.str_value or ""
            values.append({"identifier": item.identifier, "value": shown})
        return jsonify({"ok": True, "matched": True, "values": values})

    @app.post("/cases/<case_id>/rename")
    def cases_rename(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            rename_case(root, case_id, request.form.get("name", ""))
            _flash_code("S_CSE_OK02")
        except AppError as exc:
            _flash_err(exc)
        return redirect(url_for("cases_view"))

    @app.post("/cases/<case_id>/delete")
    def cases_delete(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        if request.form.get("confirm") != "1":
            _flash_code("W_CSE_BIZ01")
            return redirect(url_for("cases_view"))
        try:
            delete_case(root, case_id)
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _clear_counts(case_id)
            _flash_code("S_CSE_OK03")
        except AppError as exc:
            _flash_err(exc)
        return redirect(url_for("cases_view"))

    def _work_context(
        case_id: str,
        after: int = 0,
        tab: str | None = None,
        after_time: str | None = None,
        after_line_no: int = 0,
    ) -> dict:
        conn = open_case(root, case_id)
        try:
            meta = conn.execute("SELECT name, logformat FROM meta").fetchone()
            log_fields = conn.execute(
                "SELECT identifier, field_type, specifier, time_axis "
                "FROM log_fields ORDER BY display_order"
            ).fetchall()
            custom_names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM custom_fields ORDER BY display_order"
                )
            ]
            imports = conn.execute(
                "SELECT source_filename, read_count, inserted_count, "
                "discarded_count, duplicate_count, imported_at "
                "FROM import_files ORDER BY id"
            ).fetchall()
            named = []
            for row in conn.execute("SELECT name, spec FROM named_filters ORDER BY name"):
                try:
                    spec = json.loads(row["spec"])
                except (TypeError, json.JSONDecodeError, KeyError):
                    spec = []
                named.append({"name": row["name"], "count": len(spec), "spec": spec})
            named_aggregates = [
                {"name": name} for name in list_named_aggregates(conn)
            ]
            key = _filter_cache_key(case_id)
            cached = _get_counts(case_id)
            try:
                conditions = _session_conditions(case_id)
            except AppError as exc:
                _flash_err(exc)
                conditions = []
            except (KeyError, TypeError, ValueError):
                conditions = []
            if cached and cached.get("key") == key:
                total_row_count = cached["total_row_count"]
                match_count = cached["match_count"]
            else:
                total_row_count = conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0]
                try:
                    match_count = count_rows(conn, conditions)
                    _set_counts(case_id, total_row_count, match_count)
                except AppError as exc:
                    _flash_err(exc)
                    conditions = []
                    match_count = count_rows(conn, conditions)
                except (KeyError, TypeError, ValueError):
                    conditions = []
                    match_count = count_rows(conn, conditions)
            has_time_axis = any(field["time_axis"] for field in log_fields)
            list_sort = _effective_sort(case_id, has_time_axis)
            current_tab = tab if tab in TABS else _requested_tab()
            if current_tab == "list" or after > 0:
                try:
                    rows = fetch_rows(
                        conn,
                        conditions,
                        after,
                        sort=list_sort,
                        after_time=after_time,
                        after_line_no=after_line_no,
                    )
                except AppError as exc:
                    _flash_err(exc)
                    conditions = []
                    rows = fetch_rows(
                        conn,
                        conditions,
                        after,
                        sort=list_sort,
                        after_time=after_time,
                        after_line_no=after_line_no,
                    )
                except (KeyError, TypeError, ValueError):
                    conditions = []
                    rows = fetch_rows(
                        conn,
                        conditions,
                        after,
                        sort=list_sort,
                        after_time=after_time,
                        after_line_no=after_line_no,
                    )
            else:
                rows = []
            filter_specs = _filter_specs(case_id)
            aggregate_rows = app.config["AGGREGATE_CACHE"].get(case_id) or []
            last_import = None
            if imports:
                last_import = max(row["imported_at"] for row in imports)
            return {
                "case_id": case_id,
                "case_name": meta["name"],
                "logformat": meta["logformat"],
                "log_fields": log_fields,
                "custom_names": custom_names,
                "imports": imports,
                "match_count": match_count,
                "total_row_count": total_row_count,
                "rows": rows,
                "named": named,
                "named_aggregates": named_aggregates,
                "active_named_agg": (session.get("named_agg_by_case") or {}).get(case_id),
                "filter_specs": filter_specs,
                "aggregate_rows": aggregate_rows,
                "agg_max": max((row.get("access_count") or 0) for row in aggregate_rows)
                if aggregate_rows
                else 0,
                "agg_form": (session.get("agg_by_case") or {}).get(case_id) or {},
                "agg_groups": ((session.get("agg_by_case") or {}).get(case_id) or {}).get(
                    "groups"
                )
                or [],
                "has_time_axis": has_time_axis,
                "list_sort": list_sort,
                "last_import": last_import,
                "active_named": (session.get("named_by_case") or {}).get(case_id),
                "op_labels": OP_LABELS,
                "warn_custom_del": MSG["W_WRK_BIZ04"],
                "bulk_confirm": None,
                "import_delete_confirm": None,
                "tab": _requested_tab(),
                "import_job": app.config["IMPORT_JOBS"].status_payload(case_id),
            }
        finally:
            conn.close()

    def _work_context_busy(case_id: str) -> dict:
        jobs = app.config["IMPORT_JOBS"]
        job = jobs.get(case_id)
        log_fields = list(job.snapshot_log_fields) if job else []
        imports = list(job.snapshot_imports) if job else []
        custom_names = list(job.snapshot_custom_names) if job else []
        named = list(job.snapshot_named) if job else []
        row_count = job.snapshot_row_count if job else 0
        aggregate_rows = app.config["AGGREGATE_CACHE"].get(case_id) or []
        agg_form = (session.get("agg_by_case") or {}).get(case_id) or {}
        return {
            "case_id": case_id,
            "case_name": job.snapshot_name if job else case_id,
            "logformat": job.snapshot_logformat if job else "",
            "log_fields": log_fields,
            "custom_names": custom_names,
            "imports": imports,
            "match_count": row_count,
            "total_row_count": row_count,
            "rows": [],
            "named": named,
            "named_aggregates": [],
            "active_named_agg": (session.get("named_agg_by_case") or {}).get(case_id),
            "filter_specs": _filter_specs(case_id),
            "aggregate_rows": aggregate_rows,
            "agg_max": max((row.get("access_count") or 0) for row in aggregate_rows)
            if aggregate_rows
            else 0,
            "agg_form": agg_form,
            "agg_groups": agg_form.get("groups") or [],
            "has_time_axis": any(field.get("time_axis") for field in log_fields),
            "list_sort": "row_id",
            "last_import": job.snapshot_last_import if job else None,
            "active_named": (session.get("named_by_case") or {}).get(case_id),
            "op_labels": OP_LABELS,
            "warn_custom_del": MSG["W_WRK_BIZ04"],
            "bulk_confirm": None,
            "import_delete_confirm": None,
            "tab": "ingest",
            "import_job": jobs.status_payload(case_id),
        }

    def _render_work(case_id: str):
        jobs = app.config["IMPORT_JOBS"]
        if jobs.take_finished_notice(case_id):
            job = jobs.get(case_id)
            failed = [item for item in (job.files if job else []) if item.status == "failed"]
            if failed:
                flash(failed[0].error or MSG["E_WRK_SYS01"], "err")
            else:
                _flash_code("I_WRK_INF03")
            _clear_counts(case_id)
        try:
            after = int(request.args.get("after") or 0)
        except (TypeError, ValueError):
            after = 0
        after_time = request.args.get("after_time")
        try:
            after_line_no = int(request.args.get("after_line") or 0)
        except (TypeError, ValueError):
            after_line_no = 0
        tab = _requested_tab()
        if jobs.is_running(case_id) and tab != "ingest":
            _flash_code("W_WRK_BIZ06")
            return _work_redirect(case_id, "ingest")
        if jobs.is_running(case_id):
            return render_template("work.html", **_work_context_busy(case_id))
        try:
            context = _work_context(
                case_id, after, tab, after_time, after_line_no
            )
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        return render_template("work.html", **context)

    @app.get("/cases/<case_id>")
    def work_view(case_id: str):
        return _render_work(case_id)

    @app.get("/cases/<case_id>/more")
    def work_more(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        return _render_work(case_id)

    @app.post("/cases/<case_id>/import")
    def work_import(case_id: str):
        jobs: ImportJobStore = app.config["IMPORT_JOBS"]
        if jobs.is_running(case_id):
            _flash_code("W_WRK_BIZ06")
            return _work_redirect(case_id, "ingest")
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        queued: list[tuple[str, bytes]] | None = None
        snapshot: dict | None = None
        try:
            files = request.files.getlist("files")
            if not files or all(not item.filename for item in files):
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
            custom_names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM custom_fields ORDER BY display_order"
                )
            ]
            named = []
            for row in conn.execute("SELECT name, spec FROM named_filters ORDER BY name"):
                try:
                    spec = json.loads(row["spec"])
                except (TypeError, json.JSONDecodeError, KeyError):
                    spec = []
                named.append({"name": row["name"], "count": len(spec), "spec": spec})
            snapshot = {
                "name": meta["name"],
                "logformat": meta["logformat"],
                "logformat_head": (meta["logformat"] or "")[:80],
                "row_count": conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0],
                "file_count": len(imports),
                "last_import": max((row["imported_at"] for row in imports), default=None),
                "imports": [dict(zip(row.keys(), row)) for row in imports],
                "log_fields": [dict(zip(row.keys(), row)) for row in log_fields],
                "custom_names": custom_names,
                "named": named,
            }
            queued = accepted
        except AppError as exc:
            _flash_err(exc)
            return _work_redirect(case_id, "ingest")
        finally:
            conn.close()
        if queued is None or snapshot is None:
            return _work_redirect(case_id, "ingest")
        try:
            jobs.enqueue(case_id, queued, snapshot)
        except ValueError as exc:
            if "running" not in str(exc):
                raise
            _flash_code("W_WRK_BIZ06")
            return _work_redirect(case_id, "ingest")
        app.config["AGGREGATE_CACHE"].pop(case_id, None)
        _clear_counts(case_id)

        def invalidate(cid: str) -> None:
            app.config["AGGREGATE_CACHE"].pop(cid, None)

        jobs.start(case_id, lambda: open_case(root, case_id), invalidate)
        _flash_code("I_WRK_INF02")
        return _work_redirect(case_id, "ingest")

    @app.get("/cases/<case_id>/import/status")
    def work_import_status(case_id: str):
        if not case_exists(root, case_id):
            return jsonify({"job": None}), 404
        payload = app.config["IMPORT_JOBS"].status_payload(case_id)
        return jsonify(payload)

    @app.post("/cases/<case_id>/imports/delete")
    def work_import_delete(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        filename = request.form.get("source_filename", "")
        warning = None
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            if not filename:
                return _work_redirect(case_id, "ingest")
            exists = conn.execute(
                "SELECT 1 FROM import_files WHERE source_filename = ? LIMIT 1",
                (filename,),
            ).fetchone()
            if not exists:
                return _work_redirect(case_id, "ingest")
            if request.form.get("confirm") != "1":
                row_count = conn.execute(
                    "SELECT COUNT(*) FROM log_rows WHERE source_filename = ?",
                    (filename,),
                ).fetchone()[0]
                warning = msg("W_WRK_BIZ05", filename=filename, row_count=row_count)
            else:
                warning = None
                delete_imported_file(conn, filename)
                app.config["AGGREGATE_CACHE"].pop(case_id, None)
                _clear_counts(case_id)
                _flash_code("S_WRK_OK03")
        except AppError as exc:
            _flash_err(exc)
            return _work_redirect(case_id, "ingest")
        finally:
            conn.close()
        if warning is not None:
            flash(warning, "warn")
            context = _work_context(case_id, tab="ingest")
            context["tab"] = "ingest"
            context["import_delete_confirm"] = {
                "filename": filename,
                "warning": warning,
            }
            return render_template("work.html", **context)
        return _work_redirect(case_id, "ingest")

    @app.post("/cases/<case_id>/sort")
    def work_sort(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        _set_case_sort(case_id, request.form.get("sort") or "row_id")
        return _work_redirect(case_id, "list")

    @app.post("/cases/<case_id>/filter")
    def work_filter(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        if request.form.get("clear") == "1":
            _set_case_filters(case_id, [])
            _set_named(case_id, None)
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _clear_counts(case_id)
            return _work_redirect(case_id, "list")
        identifiers = request.form.getlist("identifier")
        operators = request.form.getlist("op")
        values = request.form.getlist("value")
        spec = []
        for encoded, operator, value in zip(identifiers, operators, values):
            try:
                source, field_type, identifier = encoded.split(":", 2)
            except ValueError:
                continue
            if source not in {"log", "custom"} or not _valid_filter_op(
                field_type, operator
            ):
                continue
            spec.append(
                {
                    "identifier": identifier,
                    "source": source,
                    "field_type": field_type,
                    "op": operator,
                    "value": value.split(",") if operator in {"in", "not_in"} else value,
                }
            )
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        conn.close()
        _set_case_filters(case_id, spec)
        _set_named(case_id, None)
        app.config["AGGREGATE_CACHE"].pop(case_id, None)
        _clear_counts(case_id)
        return _work_redirect(case_id, "list")

    @app.post("/cases/<case_id>/filter/save")
    def work_filter_save(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            save_named_filter(
                conn,
                request.form.get("name", ""),
                _case_specs(case_id),
                overwrite=request.form.get("overwrite") == "1",
            )
            _flash_code("S_WRK_OK02")
            _set_named(case_id, request.form.get("name", "").strip())
        except AppError as exc:
            _flash_err(exc)
        finally:
            conn.close()
        return _work_redirect(case_id, "list")

    @app.post("/cases/<case_id>/filter/load")
    def work_filter_load(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            spec = load_named_filter(conn, request.form.get("name", ""))
            count_rows(conn, conditions_from_spec(spec))
            _set_case_filters(case_id, spec)
            _set_named(case_id, request.form.get("name", ""))
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _clear_counts(case_id)
        except AppError as exc:
            _flash_err(exc)
        except (KeyError, TypeError, ValueError):
            _set_case_filters(case_id, [])
            _set_named(case_id, None)
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _clear_counts(case_id)
        finally:
            conn.close()
        return _work_redirect(case_id, "list")

    @app.post("/cases/<case_id>/filter/delete")
    def work_filter_delete(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            delete_named_filter(conn, request.form.get("name", ""))
            if (session.get("named_by_case") or {}).get(case_id) == request.form.get(
                "name", ""
            ):
                _set_named(case_id, None)
        except AppError as exc:
            _flash_err(exc)
        finally:
            conn.close()
        return _work_redirect(case_id, request.form.get("tab") or "list")

    @app.post("/cases/<case_id>/custom")
    def work_custom(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            add_custom_field(conn, request.form.get("name", ""))
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _clear_counts(case_id)
        except AppError as exc:
            _flash_err(exc)
        finally:
            conn.close()
        return _work_redirect(case_id, "schema")

    @app.post("/cases/<case_id>/custom/delete")
    def work_custom_delete(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        if request.form.get("confirm") != "1":
            _flash_code("W_WRK_BIZ04")
            return _work_redirect(case_id, "schema")
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            delete_custom_field(conn, request.form.get("name", ""))
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _clear_counts(case_id)
        except AppError as exc:
            _flash_err(exc)
        finally:
            conn.close()
        return _work_redirect(case_id, "schema")

    @app.post("/cases/<case_id>/cell")
    def work_cell(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            set_custom_value(
                conn,
                int(request.form["row_id"]),
                request.form["name"],
                request.form.get("value", ""),
            )
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _clear_counts(case_id)
        except (AppError, KeyError, ValueError) as exc:
            if isinstance(exc, AppError):
                _flash_err(exc)
            else:
                _flash_code("E_WRK_FMT02")
        finally:
            conn.close()
        return _work_redirect(case_id, "list")

    @app.post("/cases/<case_id>/bulk")
    def work_bulk(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        if request.form.get("confirm") != "1":
            try:
                target_count = count_rows(conn, _session_conditions(case_id))
                warning = msg("W_WRK_BIZ02", target_count=target_count)
                flash(warning, "warn")
                context = _work_context(case_id, tab="list")
                context["tab"] = "list"
                context["bulk_confirm"] = {
                    "name": request.form.get("name", ""),
                    "value": request.form.get("value", ""),
                    "warning": warning,
                }
                return render_template("work.html", **context)
            except AppError as exc:
                _flash_err(exc)
                return _work_redirect(case_id, "list")
            except (KeyError, TypeError, ValueError):
                _set_case_filters(case_id, [])
                return _work_redirect(case_id, "list")
            finally:
                conn.close()
        try:
            bulk_set_custom(
                conn,
                request.form.get("name", ""),
                request.form.get("value", ""),
                _session_conditions(case_id),
            )
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _clear_counts(case_id)
            _flash_code("S_WRK_OK01")
        except AppError as exc:
            _flash_err(exc)
        finally:
            conn.close()
        return _work_redirect(case_id, "list")

    def _apply_aggregate(conn, case_id: str, payload: dict) -> None:
        groups = []
        for item in payload.get("groups") or []:
            source, identifier = str(item).split(":", 1)
            groups.append((source, identifier))
        bucket = None
        if payload.get("use_bucket"):
            bucket = (int(payload.get("n") or "0"), payload.get("unit") or "hour")
        metrics = []
        if payload.get("metric_id"):
            metrics.append(
                Metric(payload["metric_id"], payload.get("metric_fn") or "count")
            )
        _set_agg_form(case_id, payload)
        app.config["AGGREGATE_CACHE"][case_id] = aggregate(
            conn, _session_conditions(case_id), groups, bucket, metrics
        )

    @app.post("/cases/<case_id>/aggregate")
    def work_aggregate(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            _apply_aggregate(conn, case_id, _agg_payload_from_request())
        except AppError as exc:
            _flash_err(exc)
        except ValueError:
            _flash_code("E_WRK_RNG01")
        finally:
            conn.close()
        return _work_redirect(case_id, "agg")

    @app.post("/cases/<case_id>/aggregate/save")
    def work_aggregate_save(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            save_named_aggregate(
                conn,
                request.form.get("name", ""),
                _agg_payload_from_request(),
                overwrite=request.form.get("overwrite") == "1",
            )
            _flash_code("S_WRK_OK04")
            _set_named_agg(case_id, request.form.get("name", "").strip())
            _set_agg_form(case_id, _agg_payload_from_request())
        except AppError as exc:
            _flash_err(exc)
        finally:
            conn.close()
        return _work_redirect(case_id, "agg")

    @app.post("/cases/<case_id>/aggregate/load")
    def work_aggregate_load(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            payload = _agg_payload_from_mapping(
                load_named_aggregate(conn, request.form.get("name", ""))
            )
            _apply_aggregate(conn, case_id, payload)
            _set_named_agg(case_id, request.form.get("name", ""))
        except AppError as exc:
            _flash_err(exc)
        except ValueError:
            _flash_code("E_WRK_RNG01")
        finally:
            conn.close()
        return _work_redirect(case_id, "agg")

    @app.post("/cases/<case_id>/aggregate/delete")
    def work_aggregate_delete(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            delete_named_aggregate(conn, request.form.get("name", ""))
            if (session.get("named_agg_by_case") or {}).get(case_id) == request.form.get(
                "name", ""
            ):
                _set_named_agg(case_id, None)
        except AppError as exc:
            _flash_err(exc)
        finally:
            conn.close()
        return _work_redirect(case_id, "agg")

    @app.post("/cases/<case_id>/export/rows")
    def export_rows(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            _flash_err(exc)
            return redirect(url_for("cases_view"))
        try:
            cached = _get_counts(case_id)
            key = _filter_cache_key(case_id)
            match = (
                cached["match_count"]
                if cached and cached.get("key") == key
                else None
            )
            has_time = any(
                row["time_axis"]
                for row in conn.execute("SELECT time_axis FROM log_fields")
            )
            sort = _effective_sort(case_id, bool(has_time))
            data = rows_csv_bytes(
                conn, _session_conditions(case_id), match_count=match, sort=sort
            )
        except AppError as exc:
            _flash_err(exc)
            return _work_redirect(case_id, "list")
        finally:
            conn.close()
        return Response(
            data,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=rows.csv"},
        )

    @app.post("/cases/<case_id>/export/aggregate")
    def export_aggregate(case_id: str):
        blocked = _busy_redirect(case_id)
        if blocked:
            return blocked
        rows = app.config["AGGREGATE_CACHE"].get(case_id) or []
        if not rows:
            try:
                context = _work_context(case_id, tab="agg")
            except AppError as exc:
                _flash_err(exc)
                return redirect(url_for("cases_view"))
            context["tab"] = "agg"
            return render_template("work.html", **context), 400
        return Response(
            aggregate_csv_bytes(rows),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=aggregate.csv"},
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    data_dir = Path.cwd() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    from logcase.import_jobs import clean_staging

    clean_staging(data_dir)
    if use_duckdb():
        convert_all_cases(data_dir)
    app = create_app(data_dir)
    host = listen_host()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((host, args.port))
        finally:
            probe.close()
        print(f"http://127.0.0.1:{args.port}/", flush=True)
        app.run(host=host, port=args.port, threaded=False)
    except (OSError, OverflowError):
        print(MSG["E_LCH_SYS01"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
