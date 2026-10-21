from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from access_log_db.errors import AppError
from access_log_db.messages import MSG

PAGE_SIZE = 100


@dataclass
class Condition:
    identifier: str
    source: str
    field_type: str
    op: str
    value: str | list[str] | None


def _parse_int(text: str) -> int:
    try:
        return int(text)
    except (TypeError, ValueError) as exc:
        raise AppError("E_WRK_FMT02", MSG["E_WRK_FMT02"]) from exc


def _parse_dt(text: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    raise AppError("E_WRK_FMT03", MSG["E_WRK_FMT03"])


def _where(conditions: list[Condition]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for cond in conditions:
        sql, extra = _one(cond)
        clauses.append(sql)
        params.extend(extra)
    if not clauses:
        return "1=1", []
    return " AND ".join(clauses), params


def _one(cond: Condition) -> tuple[str, list[Any]]:
    if cond.field_type == "datetime":
        wall = "substr(r.time_abs, 1, 19)"
        if cond.op == "unset":
            return "r.time_abs IS NULL", []
        target = _parse_dt(str(cond.value))
        ops = {
            "eq": "=",
            "before": "<",
            "on_or_after": ">=",
            "after": ">",
            "on_or_before": "<=",
        }
        return f"(r.time_abs IS NOT NULL AND {wall} {ops[cond.op]} ?)", [target]
    if cond.source == "custom":
        exists = (
            "EXISTS (SELECT 1 FROM custom_values c "
            "WHERE c.row_id = r.row_id AND c.name = ? AND {pred})"
        )
        key = cond.identifier
        col_s, col_u = "c.str_value", "c.is_unset"
    else:
        exists = (
            "EXISTS (SELECT 1 FROM log_values v "
            "WHERE v.row_id = r.row_id AND v.identifier = ? AND {pred})"
        )
        key = cond.identifier
        col_s, col_u = "v.str_value", "v.is_unset"
        col_n = "v.num_value"
    if cond.field_type == "number":
        if cond.op == "unset":
            return exists.format(pred=f"{col_u} = 1"), [key]
        number = _parse_int(str(cond.value))
        ops = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
        pred = f"{col_u} = 0 AND {col_n} {ops[cond.op]} ?"
        return exists.format(pred=pred), [key, number]
    if cond.op == "contains":
        pred = f"{col_s} IS NOT NULL AND instr({col_s}, ?) > 0"
        return exists.format(pred=pred), [key, cond.value]
    if cond.op == "not_contains":
        pred = f"{col_s} IS NOT NULL AND instr({col_s}, ?) = 0"
        return exists.format(pred=pred), [key, cond.value]
    if cond.op == "starts":
        pred = f"{col_s} IS NOT NULL AND substr({col_s}, 1, length(?)) = ?"
        return exists.format(pred=pred), [key, cond.value, cond.value]
    if cond.op == "not_starts":
        pred = f"{col_s} IS NOT NULL AND substr({col_s}, 1, length(?)) != ?"
        return exists.format(pred=pred), [key, cond.value, cond.value]
    if cond.op == "gte":
        pred = f"{col_s} IS NOT NULL AND {col_s} >= ?"
        return exists.format(pred=pred), [key, cond.value]
    if cond.op == "lte":
        pred = f"{col_s} IS NOT NULL AND {col_s} <= ?"
        return exists.format(pred=pred), [key, cond.value]
    if cond.op == "in":
        values = list(cond.value or [])
        placeholders = ",".join("?" * len(values)) or "NULL"
        pred = f"{col_s} IN ({placeholders})"
        return exists.format(pred=pred), [key, *values]
    raise ValueError(f"unknown op {cond.op}")


def count_rows(conn: sqlite3.Connection, conditions: list[Condition]) -> int:
    where, params = _where(conditions)
    sql = f"SELECT COUNT(*) FROM log_rows r WHERE {where}"
    return conn.execute(sql, params).fetchone()[0]


def fetch_rows(
    conn: sqlite3.Connection,
    conditions: list[Condition],
    after_row_id: int,
    limit: int = PAGE_SIZE,
) -> list[dict]:
    where, params = _where(conditions)
    sql = (
        f"SELECT r.row_id, r.source_filename, r.line_no, r.time_display "
        f"FROM log_rows r WHERE r.row_id > ? AND {where} "
        f"ORDER BY r.row_id LIMIT ?"
    )
    rows = conn.execute(sql, [after_row_id, *params, limit]).fetchall()
    log_fields = [
        r["identifier"]
        for r in conn.execute(
            "SELECT identifier, field_type FROM log_fields ORDER BY display_order"
        )
    ]
    field_types = {
        r["identifier"]: r["field_type"]
        for r in conn.execute("SELECT identifier, field_type FROM log_fields")
    }
    custom_names = [
        r["name"]
        for r in conn.execute("SELECT name FROM custom_fields ORDER BY display_order")
    ]
    row_ids = [row["row_id"] for row in rows]
    values_by_row: dict[int, dict[str, sqlite3.Row]] = {}
    customs_by_row: dict[int, dict[str, sqlite3.Row]] = {}
    if row_ids:
        placeholders = ",".join("?" for _ in row_ids)
        for value in conn.execute(
            "SELECT row_id, identifier, is_unset, str_value, num_value "
            f"FROM log_values WHERE row_id IN ({placeholders})",
            row_ids,
        ):
            values_by_row.setdefault(value["row_id"], {})[value["identifier"]] = value
        for value in conn.execute(
            "SELECT row_id, name, is_unset, str_value "
            f"FROM custom_values WHERE row_id IN ({placeholders})",
            row_ids,
        ):
            customs_by_row.setdefault(value["row_id"], {})[value["name"]] = value
    out: list[dict] = []
    for row in rows:
        item: dict = {}
        values = values_by_row.get(row["row_id"], {})
        for ident in log_fields:
            if field_types[ident] == "datetime":
                item[ident] = row["time_display"]
                continue
            val = values.get(ident)
            if val is None or val["is_unset"]:
                item[ident] = None
            elif val["num_value"] is not None:
                item[ident] = val["num_value"]
            else:
                item[ident] = val["str_value"]
        customs = customs_by_row.get(row["row_id"], {})
        for name in custom_names:
            val = customs.get(name)
            if val is None or val["is_unset"]:
                item[name] = None
            else:
                item[name] = val["str_value"]
        item["row_id"] = row["row_id"]
        item["source_filename"] = row["source_filename"]
        item["line_no"] = row["line_no"]
        out.append(item)
    return out
