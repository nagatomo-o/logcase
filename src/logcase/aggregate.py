from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from logcase.errors import AppError
from logcase.filters import Condition, _where, count_rows, fetch_rows
from logcase.messages import MSG

_PAGE = 5000
_UNITS = ("second", "minute", "hour", "day")


@dataclass
class Metric:
    identifier: str
    fn: str


def _has_time_axis(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM log_fields WHERE time_axis = 1"
    ).fetchone()
    return row is not None


def _bucket_start(wall: str, n: int, unit: str) -> str:
    dt = datetime.strptime(wall, "%Y-%m-%dT%H:%M:%S")
    unit_seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}.get(unit)
    if unit_seconds is None:
        raise AppError("E_WRK_RNG01", MSG["E_WRK_RNG01"])
    epoch = datetime(1970, 1, 1)
    elapsed = dt - epoch
    total_seconds = elapsed.days * 86400 + elapsed.seconds
    bucket_seconds = n * unit_seconds
    start_seconds = (total_seconds // bucket_seconds) * bucket_seconds
    start = epoch + timedelta(seconds=start_seconds)
    return start.strftime("%Y-%m-%dT%H:%M:%S")


def _page_time_abs(
    conn: sqlite3.Connection,
    conditions: list[Condition],
    after_row_id: int,
    limit: int,
) -> dict[int, str | None]:
    where, params = _where(conditions)
    sql = (
        "SELECT r.row_id, r.time_abs FROM log_rows r "
        f"WHERE r.row_id > ? AND {where} ORDER BY r.row_id LIMIT ?"
    )
    rows = conn.execute(sql, [after_row_id, *params, limit]).fetchall()
    return {int(row["row_id"]): row["time_abs"] for row in rows}


def _blank_group() -> dict[str, Any]:
    return {
        "access_count": 0,
        "sums": defaultdict(int),
        "counts": defaultdict(int),
    }


def aggregate(
    conn: sqlite3.Connection,
    conditions: list[Condition],
    group_ids: list[tuple[str, str]],
    bucket: tuple[int, str] | None,
    metrics: list[Metric],
) -> list[dict]:
    if bucket is not None:
        n, unit = bucket
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise AppError("E_WRK_RNG01", MSG["E_WRK_RNG01"])
        if not _has_time_axis(conn):
            raise AppError("E_WRK_CMB01", MSG["E_WRK_CMB01"])
        if unit not in _UNITS:
            raise AppError("E_WRK_RNG01", MSG["E_WRK_RNG01"])
    total = count_rows(conn, conditions)
    sum_ids = {metric.identifier for metric in metrics if metric.fn in ("sum", "avg")}
    metric_ids: list[str] = []
    seen_ids: set[str] = set()
    for metric in metrics:
        if metric.identifier in seen_ids:
            continue
        seen_ids.add(metric.identifier)
        metric_ids.append(metric.identifier)
    groups: dict[tuple, dict[str, Any]] = defaultdict(_blank_group)
    after = 0
    seen = 0
    while True:
        chunk = fetch_rows(conn, conditions, after, limit=_PAGE)
        if not chunk:
            break
        times = (
            _page_time_abs(conn, conditions, after, _PAGE)
            if bucket is not None
            else {}
        )
        for row in chunk:
            if bucket is not None:
                raw_abs = times.get(row["row_id"])
                if raw_abs is None:
                    continue
                bstart = _bucket_start(raw_abs[:19], bucket[0], bucket[1])
            else:
                bstart = None
            key_parts: list[Any] = []
            for _source, ident in group_ids:
                key_parts.append(row.get(ident))
            key_parts.append(bstart)
            g = groups[tuple(key_parts)]
            g["access_count"] += 1
            for ident in metric_ids:
                val = row.get(ident)
                if val is None:
                    continue
                g["counts"][ident] += 1
                if ident in sum_ids:
                    g["sums"][ident] += val
        after = chunk[-1]["row_id"]
        seen += len(chunk)
        if seen >= total:
            break
    out: list[dict] = []
    for key, g in groups.items():
        item: dict[str, Any] = {}
        for i, (_source, ident) in enumerate(group_ids):
            item[ident] = key[i]
        if bucket is not None:
            item["bucket_start"] = key[-1]
        item["access_count"] = g["access_count"]
        for metric in metrics:
            name = f"{metric.identifier}_{metric.fn}"
            counted = g["counts"][metric.identifier]
            if metric.fn == "count":
                item[name] = counted
            elif metric.fn == "sum":
                item[name] = g["sums"][metric.identifier] if counted else None
            elif metric.fn == "avg":
                item[name] = (g["sums"][metric.identifier] / counted) if counted else None
        out.append(item)
    out.sort(key=lambda d: tuple(str(v) for v in d.values()))
    return out
