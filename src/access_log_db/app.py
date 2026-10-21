from __future__ import annotations

import argparse
import secrets
import socket
import sys
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from access_log_db.aggregate import Metric, aggregate
from access_log_db.csv_export import aggregate_csv_bytes, rows_csv_bytes
from access_log_db.custom_fields import (
    add_custom_field,
    bulk_set_custom,
    delete_custom_field,
    set_custom_value,
)
from access_log_db.errors import AppError
from access_log_db.filters import Condition, count_rows, fetch_rows
from access_log_db.import_log import import_file, import_gzip_bytes
from access_log_db.messages import MSG, msg
from access_log_db.named_filters import (
    conditions_from_spec,
    list_named_filters,
    load_named_filter,
    save_named_filter,
)
from access_log_db.store import (
    create_case,
    delete_case,
    list_cases,
    open_case,
    rename_case,
)


def _allowed_filename(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(".gz") or lower.endswith("access_log") or "." not in Path(name).name


def _case_specs(case_id: str) -> list[dict]:
    return (session.get("filters_by_case") or {}).get(case_id) or []


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


def _valid_filter_op(field_type: str, operator: str) -> bool:
    allowed = {
        "string": {"contains", "not_contains", "starts", "not_starts", "in", "gte", "lte"},
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

    @app.get("/")
    def cases_view():
        return render_template(
            "cases.html",
            cases=list_cases(root),
            warn_delete=MSG["W_CSE_BIZ01"],
        )

    @app.post("/cases")
    def cases_create():
        try:
            create_case(root, request.form.get("name", ""), request.form.get("logformat", ""))
            flash(MSG["S_CSE_OK01"])
        except AppError as exc:
            flash(exc.message)
        return redirect(url_for("cases_view"))

    @app.post("/cases/<case_id>/rename")
    def cases_rename(case_id: str):
        try:
            rename_case(root, case_id, request.form.get("name", ""))
            flash(MSG["S_CSE_OK02"])
        except AppError as exc:
            flash(exc.message)
        return redirect(url_for("cases_view"))

    @app.post("/cases/<case_id>/delete")
    def cases_delete(case_id: str):
        if request.form.get("confirm") != "1":
            flash(MSG["W_CSE_BIZ01"])
            return redirect(url_for("cases_view"))
        try:
            delete_case(root, case_id)
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            flash(MSG["S_CSE_OK03"])
        except AppError as exc:
            flash(exc.message)
        return redirect(url_for("cases_view"))

    def _work_context(case_id: str, after: int = 0) -> dict:
        conn = open_case(root, case_id)
        try:
            meta = conn.execute("SELECT name, logformat FROM meta").fetchone()
            log_fields = conn.execute(
                "SELECT identifier, field_type, time_axis "
                "FROM log_fields ORDER BY display_order"
            ).fetchall()
            custom_names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM custom_fields ORDER BY display_order"
                )
            ]
            imports = conn.execute(
                "SELECT source_filename, read_count, imported_at "
                "FROM import_files ORDER BY id"
            ).fetchall()
            try:
                conditions = _session_conditions(case_id)
                match_count = count_rows(conn, conditions)
                rows = fetch_rows(conn, conditions, after)
            except AppError as exc:
                _set_case_filters(case_id, [])
                flash(exc.message)
                conditions = []
                match_count = count_rows(conn, conditions)
                rows = fetch_rows(conn, conditions, after)
            except (KeyError, TypeError, ValueError):
                _set_case_filters(case_id, [])
                conditions = []
                match_count = count_rows(conn, conditions)
                rows = fetch_rows(conn, conditions, after)
            return {
                "case_id": case_id,
                "case_name": meta["name"],
                "logformat": meta["logformat"],
                "log_fields": log_fields,
                "custom_names": custom_names,
                "imports": imports,
                "match_count": match_count,
                "rows": rows,
                "named": list_named_filters(conn),
                "aggregate_rows": app.config["AGGREGATE_CACHE"].get(case_id) or [],
                "warn_custom_del": MSG["W_WRK_BIZ04"],
                "bulk_confirm": None,
            }
        finally:
            conn.close()

    def _render_work(case_id: str):
        try:
            after = int(request.args.get("after") or 0)
        except (TypeError, ValueError):
            after = 0
        try:
            context = _work_context(case_id, after)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        return render_template("work.html", **context)

    @app.get("/cases/<case_id>")
    def work_view(case_id: str):
        return _render_work(case_id)

    @app.get("/cases/<case_id>/more")
    def work_more(case_id: str):
        return _render_work(case_id)

    @app.post("/cases/<case_id>/import")
    def work_import(case_id: str):
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        files = request.files.getlist("files")
        if not files or all(not item.filename for item in files):
            conn.close()
            flash(MSG["E_WRK_REQ01"])
            return redirect(url_for("work_view", case_id=case_id))
        existing = {
            row[0] for row in conn.execute("SELECT source_filename FROM import_files")
        }
        confirm = request.form.get("confirm_same_name") == "1"
        try:
            for uploaded in files:
                name = Path(uploaded.filename or "").name
                if not _allowed_filename(name):
                    flash(MSG["E_WRK_FMT01"])
                    continue
                if name in existing and not confirm:
                    flash(MSG["W_WRK_BIZ01"])
                    continue
                data = uploaded.read()
                if name.lower().endswith(".gz"):
                    result = import_gzip_bytes(conn, name, data)
                else:
                    text = data.decode("utf-8", errors="replace")
                    result = import_file(conn, name, text.splitlines(keepends=True))
                app.config["AGGREGATE_CACHE"].pop(case_id, None)
                flash(
                    msg(
                        "I_WRK_INF01",
                        read_count=result.read_count,
                        inserted_count=result.inserted_count,
                        discarded_count=result.discarded_count,
                        duplicate_count=result.duplicate_count,
                    )
                )
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/filter")
    def work_filter(case_id: str):
        if request.form.get("clear") == "1":
            _set_case_filters(case_id, [])
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            return redirect(url_for("work_view", case_id=case_id))
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
                    "value": value.split(",") if operator == "in" else value,
                }
            )
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        try:
            count_rows(conn, conditions_from_spec(spec))
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("work_view", case_id=case_id))
        finally:
            conn.close()
        _set_case_filters(case_id, spec)
        app.config["AGGREGATE_CACHE"].pop(case_id, None)
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/filter/save")
    def work_filter_save(case_id: str):
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        try:
            save_named_filter(
                conn,
                request.form.get("name", ""),
                _case_specs(case_id),
                overwrite=request.form.get("overwrite") == "1",
            )
            flash(MSG["S_WRK_OK02"])
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/filter/load")
    def work_filter_load(case_id: str):
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        try:
            spec = load_named_filter(conn, request.form.get("name", ""))
            count_rows(conn, conditions_from_spec(spec))
            _set_case_filters(case_id, spec)
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
        except AppError as exc:
            flash(exc.message)
        except (KeyError, TypeError, ValueError):
            _set_case_filters(case_id, [])
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/custom")
    def work_custom(case_id: str):
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        try:
            add_custom_field(conn, request.form.get("name", ""))
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/custom/delete")
    def work_custom_delete(case_id: str):
        if request.form.get("confirm") != "1":
            flash(MSG["W_WRK_BIZ04"])
            return redirect(url_for("work_view", case_id=case_id))
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        try:
            delete_custom_field(conn, request.form.get("name", ""))
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/cell")
    def work_cell(case_id: str):
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        try:
            set_custom_value(
                conn,
                int(request.form["row_id"]),
                request.form["name"],
                request.form.get("value", ""),
            )
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
        except (AppError, KeyError, ValueError) as exc:
            flash(exc.message if isinstance(exc, AppError) else MSG["E_WRK_FMT02"])
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/bulk")
    def work_bulk(case_id: str):
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        if request.form.get("confirm") != "1":
            try:
                target_count = count_rows(conn, _session_conditions(case_id))
                warning = msg("W_WRK_BIZ02", target_count=target_count)
                flash(warning)
                context = _work_context(case_id)
                context["bulk_confirm"] = {
                    "name": request.form.get("name", ""),
                    "value": request.form.get("value", ""),
                    "warning": warning,
                }
                return render_template("work.html", **context)
            except AppError as exc:
                flash(exc.message)
                return redirect(url_for("work_view", case_id=case_id))
            except (KeyError, TypeError, ValueError):
                _set_case_filters(case_id, [])
                return redirect(url_for("work_view", case_id=case_id))
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
            flash(MSG["S_WRK_OK01"])
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/aggregate")
    def work_aggregate(case_id: str):
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        try:
            groups = []
            for item in request.form.getlist("group"):
                source, identifier = item.split(":", 1)
                groups.append((source, identifier))
            bucket = None
            if request.form.get("use_bucket"):
                bucket = (
                    int(request.form.get("n") or "0"),
                    request.form.get("unit") or "hour",
                )
            metrics = []
            if request.form.get("metric_id"):
                metrics.append(
                    Metric(
                        request.form["metric_id"],
                        request.form.get("metric_fn") or "count",
                    )
                )
            app.config["AGGREGATE_CACHE"][case_id] = aggregate(
                conn, _session_conditions(case_id), groups, bucket, metrics
            )
        except AppError as exc:
            flash(exc.message)
        except ValueError:
            flash(MSG["E_WRK_RNG01"])
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/export/rows")
    def export_rows(case_id: str):
        try:
            conn = open_case(root, case_id)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        try:
            data = rows_csv_bytes(conn, _session_conditions(case_id))
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("work_view", case_id=case_id))
        finally:
            conn.close()
        return Response(
            data,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=rows.csv"},
        )

    @app.post("/cases/<case_id>/export/aggregate")
    def export_aggregate(case_id: str):
        rows = app.config["AGGREGATE_CACHE"].get(case_id) or []
        if not rows:
            try:
                context = _work_context(case_id)
            except AppError as exc:
                flash(exc.message)
                return redirect(url_for("cases_view"))
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
    app = create_app(data_dir)
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", args.port))
        finally:
            probe.close()
        print(f"http://127.0.0.1:{args.port}/", flush=True)
        app.run(host="127.0.0.1", port=args.port, threaded=False)
    except (OSError, OverflowError):
        print(MSG["E_LCH_SYS01"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
