# フィルタ検索と検索後遷移の高速化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Time 範囲＋文字列完全一致の検索を値／時刻の索引で引き、検索後の作業画面遷移では件数 COUNT と不要な行取得を繰り返さない。

**Architecture:** 日時比較は `substr(time_abs,1,19)` のまま式索引を足す。文字列 `eq` / `ne` / `in` / `not_in` は `row_id IN (SELECT ...)` に変える。作業画面はセッションに件数を持ち、一覧と続き以外では行を取らない。

**Tech Stack:** Python 3.12、sqlite3、Flask、pytest。追加依存なし。

## Global Constraints

- 仕様の正は `docs/superpowers/specs/2026-10-24-filter-nav-perf-design.md`
- 一致規則は変えない。日時は `substr(r.time_abs, 1, 19)` を維持する
- 含む／始まる／以上／以下と数値演算子は変えない
- 案件の作り直しはしない。無い索引は `open_case` で足す
- CSV 進行表示はしない。チャンク 5000 と BOM は変えない
- テストはリポジトリルートで `python -m pytest`
- ユーザーが明示するまで git commit しない。各タスク末尾の Commit ステップはスキップする

## File Structure

```
src/logcase/filters.py          # 文字列完全一致を IN 副問い合わせへ
tests/test_filters.py                 # Time+Method、after の TZ、ne 未設定
src/logcase/store.py            # PRAGMA・式索引・ANALYZE
tests/test_store.py                   # 索引が無い DB を open すると作られる
src/logcase/app.py              # 件数キャッシュ、非一覧は行を取らない
src/logcase/csv_export.py       # match_count があれば COUNT 省略
tests/test_app.py                     # 2度目 COUNT なし、ingest で一覧 SELECT なし
docs/方式設計/A07-可用性・性能・運用監視.md
```

---

### Task 1: 文字列完全一致の SQL を IN 副問い合わせにする

**Files:**
- Modify: `src/logcase/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: 既存 `Condition`、`count_rows` / `fetch_rows` / `_where` / `_one`
- Produces: `field_type == "string"` かつ `op in {eq,ne,in,not_in}` のとき `r.row_id IN|NOT IN (SELECT row_id FROM log_values|custom_values WHERE key=? AND str_value ...)`。日時・含む・数値の SQL 形は変えない

- [ ] **Step 1: Write the failing tests**

`tests/test_filters.py` の末尾に追加する。既存 `test_string_ne_keeps_unset_custom_values` は残す。import を `from logcase.filters import Condition, count_rows, fetch_rows, _one` にする。

```python
def test_datetime_after_excludes_exact_wall_second(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Time", "log", "datetime", "after", "2024-10-10T12:00:00")]
    assert count_rows(conn, conds) == 2
    ids = [r["row_id"] for r in fetch_rows(conn, conds, after_row_id=0)]
    assert ids == [2, 3]
    conn.close()


def test_time_on_or_after_and_request_method_eq(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "200"),
            '127.0.0.1 - - [10/Oct/2024:13:00:00 +0900] "POST /b HTTP/1.1" 200 10\n',
            '127.0.0.1 - - [11/Oct/2024:12:00:00 +0900] "GET /c HTTP/1.1" 200 10\n',
        ],
    )
    conds = [
        Condition("Time", "log", "datetime", "on_or_after", "2024-10-10T13:00:00"),
        Condition("Request-Method", "log", "string", "eq", "POST"),
    ]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert rows[0]["Request-Method"] == "POST"
    assert rows[0]["Request-Path"] == "/b"
    conn.close()
```

`_conn` の 3 行は 12:00 GET /index、13:00 GET /api、翌日 12:00 GET /index。`after 12:00:00` は壁時計が後の 2 行。`time_abs` は `+09:00` 付きなので、`time_abs > ?` に変えると 12:00 の行まで残ってしまう。このテストが仕様の「substr を維持」を固定する。

同じファイルに SQL 形の RED を足す。`_one` を import する。

```python
def test_string_eq_sql_uses_in_subquery():
    sql, params = _one(Condition("Request-Method", "log", "string", "eq", "POST"))
    assert "row_id IN (SELECT" in sql
    assert "EXISTS" not in sql
    assert params == ["Request-Method", "POST"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_filters.py::test_string_eq_sql_uses_in_subquery tests/test_filters.py::test_datetime_after_excludes_exact_wall_second tests/test_filters.py::test_time_on_or_after_and_request_method_eq -v`

Expected: `test_string_eq_sql_uses_in_subquery` が FAIL（いまは `EXISTS`）。日時と複合条件は現行でも PASS してよい。

- [ ] **Step 3: Write minimal implementation**

`src/logcase/filters.py` の `_one` で、数値分岐のあと・`contains` の前にある `eq` / `ne` / `in` / `not_in` を次に置き換える。日時分岐と数値分岐と含む系統は動かさない。`exists` 変数は含む系統が使うので残す。

```python
    if cond.field_type == "string" and cond.op in {"eq", "ne", "in", "not_in"}:
        if cond.source == "custom":
            table, key_col, alias = "custom_values", "name", "c"
        else:
            table, key_col, alias = "log_values", "identifier", "v"
        if cond.op in {"eq", "ne"}:
            inner = (
                f"SELECT {alias}.row_id FROM {table} {alias} "
                f"WHERE {alias}.{key_col} = ? AND {alias}.str_value = ?"
            )
            params = [key, cond.value]
            if cond.op == "ne":
                return f"r.row_id NOT IN ({inner})", params
            return f"r.row_id IN ({inner})", params
        values = list(cond.value or [])
        placeholders = ",".join("?" * len(values)) or "NULL"
        inner = (
            f"SELECT {alias}.row_id FROM {table} {alias} "
            f"WHERE {alias}.{key_col} = ? AND {alias}.str_value IN ({placeholders})"
        )
        params = [key, *values]
        if cond.op == "not_in":
            return f"r.row_id NOT IN ({inner})", params
        return f"r.row_id IN ({inner})", params
```

既存の `if cond.op == "eq":` / `"ne":` / `in`/`not_in` ブロックは削除する（この分岐が先に返す）。数値 `eq`/`ne` は上の `field_type == "number"` で既に return している。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -c "from logcase.filters import Condition, _one; print(_one(Condition('Request-Method','log','string','eq','POST')))"`

Expected: `row_id IN (SELECT` と `log_values`。`EXISTS` は含まれない。

Run: `python -m pytest tests/test_filters.py -v`

Expected: PASS（新規 2 件と既存の `ne` 未設定・`eq`・`not_in`・`contains`・日時）。

- [ ] **Step 5: Commit**

スキップ。ユーザーが明示するまで commit しない。

---

### Task 2: 接続時 PRAGMA と Time 式索引

**Files:**
- Modify: `src/logcase/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: 既存 `open_case` / `init_schema` / `SCHEMA`
- Produces: `open_case` が WAL / synchronous=NORMAL / cache_size=-262144 / temp_store=MEMORY を出す。`idx_log_rows_time_wall` が無ければ作成して `ANALYZE` を 1 回。新規 SCHEMA にも同じ索引を含める

- [ ] **Step 1: Write the failing test**

`tests/test_store.py` の末尾に追加する。

```python
def test_open_case_creates_missing_time_wall_index(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    path = tmp_path / f"{cid}.sqlite"
    raw = sqlite3.connect(path)
    raw.execute("DROP INDEX IF EXISTS idx_log_rows_time_wall")
    raw.commit()
    raw.close()
    conn = open_case(tmp_path, cid)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_log_rows_time_wall'"
    ).fetchone()
    assert row is not None
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store.py::test_open_case_creates_missing_time_wall_index -v`

Expected: FAIL（DROP した索引が戻らない）。SCHEMA に既に同名索引を足してからテストすると、DROP 後の `open_case` が戻さず FAIL する。PASS なら `open_case` が先に直っているのでテスト条件を疑う。

- [ ] **Step 3: Write minimal implementation**

`src/logcase/store.py` の `SCHEMA` で `CREATE INDEX idx_log_values_num` の直後へ次を足す。

```sql
CREATE INDEX idx_log_rows_time_wall ON log_rows(substr(time_abs, 1, 19));
```

同じファイルに `_prepare_conn` を足し、`open_case` から呼ぶ。`create_case` の一時接続は今どおり閉じる（次の `open_case` で prepare する）。

```python
def _prepare_conn(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-262144")
    conn.execute("PRAGMA temp_store=MEMORY")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_log_rows_time_wall'"
    ).fetchone()
    if exists is None:
        conn.execute(
            "CREATE INDEX idx_log_rows_time_wall ON log_rows(substr(time_abs, 1, 19))"
        )
        conn.execute("ANALYZE")
        conn.commit()


def open_case(data_dir: Path, case_id: str) -> sqlite3.Connection:
    if _public_case_id(case_id) is None:
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"])
    path = case_path(data_dir, case_id)
    if not path.is_file():
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"])
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _prepare_conn(conn)
    return conn
```

`open_case` 内の単独 `PRAGMA foreign_keys=ON` は `_prepare_conn` に移す。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_store.py tests/test_filters.py -v`

Expected: PASS。

- [ ] **Step 5: Commit**

スキップ。ユーザーが明示するまで commit しない。

---

### Task 3: 件数キャッシュ・非一覧は行を取らない・Export の COUNT 省略・文書

**Files:**
- Modify: `src/logcase/app.py`
- Modify: `src/logcase/csv_export.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_csv_export.py`（署名変更の回帰だけ。新テストは任意）
- Modify: `docs/方式設計/A07-可用性・性能・運用監視.md`

**Interfaces:**
- Consumes: Task 1 の `_where`、Task 2 の `open_case`
- Produces: セッション `counts_by_case[case_id] = {key, total_row_count, match_count}`。`key` は適用中フィルタ spec の JSON。一覧と `after>0` 以外は `fetch_rows` しない。`rows_csv_bytes(..., match_count=None)` は与えられた件数を使い COUNT しない

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py` の末尾に追加する。

```python
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
```

`test_second_work_get_skips_count_sql` は、パッチ対象が `app.py` の `from logcase.store import open_case` 束縛なら `logcase.app.open_case` を差し替える。`create_app` クロージャが `open_case` をグローバル参照するので、この差し替えで `_work_context` の接続にトレースが付く。

実装前は 2 度目の GET でも `COUNT(*)` とフィルタ付き COUNT が走るので FAIL。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py::test_second_work_get_skips_count_sql tests/test_app.py::test_ingest_tab_skips_log_row_page_select -v`

Expected: FAIL（COUNT または `ORDER BY r.row_id` が残る）。

- [ ] **Step 3: Write minimal implementation**

`src/logcase/csv_export.py` の `rows_csv_bytes` を次にする。`write_rows_csv` は変えない。

```python
def rows_csv_bytes(
    conn: sqlite3.Connection,
    conditions: list[Condition],
    match_count: int | None = None,
) -> bytes:
    headers = _headers(conn)
    buf = io.StringIO()
    writer = _writer(buf)
    writer.writerow(headers)
    total = count_rows(conn, conditions) if match_count is None else match_count
    after = 0
    written = 0
    while written < total:
        chunk = fetch_rows(conn, conditions, after, limit=5000)
        if not chunk:
            break
        for row in chunk:
            writer.writerow(_row_values(row, headers))
        written += len(chunk)
        after = chunk[-1]["row_id"]
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
```

`src/logcase/app.py` に件数キャッシュを足す。`json` は既存 import。

```python
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
    data = dict(session.get("counts_by_case") or {})
    data.pop(case_id, None)
    session["counts_by_case"] = data


def _invalidate_case(case_id: str) -> None:
    _clear_counts(case_id)
```

`create_app` 内の `app.config["AGGREGATE_CACHE"].pop(case_id, None)` をすべて次に置き換える（集計キャッシュも同時に捨てる）。

```python
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _clear_counts(case_id)
```

案件削除の pop も同じ。フィルタ適用／解除／名前付き呼び出しの pop も同じ（仕様どおり破棄し、続く GET で 1 回 COUNT する）。

`_work_context` の件数と行取得を次にする。引数に `tab` を足す。

```python
    def _work_context(case_id: str, after: int = 0, tab: str | None = None) -> dict:
        ...
            key = _filter_cache_key(case_id)
            cached = _get_counts(case_id)
            try:
                conditions = _session_conditions(case_id)
            except ...  # 既存の例外処理は維持し、壊れたら conditions=[] にする
            if cached and cached.get("key") == key:
                total_row_count = cached["total_row_count"]
                match_count = cached["match_count"]
            else:
                total_row_count = conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0]
                try:
                    match_count = count_rows(conn, conditions)
                except AppError as exc:
                    _flash_err(exc)
                    conditions = []
                    match_count = count_rows(conn, conditions)
                except (KeyError, TypeError, ValueError):
                    conditions = []
                    match_count = count_rows(conn, conditions)
                _set_counts(case_id, total_row_count, match_count)
            current_tab = tab if tab in TABS else _requested_tab()
            if current_tab == "list" or after > 0:
                rows = fetch_rows(conn, conditions, after)
            else:
                rows = []
```

`_render_work` は `_work_context(case_id, after, _requested_tab())` を呼ぶ。他の `_work_context(case_id)` 呼び出しは tab を明示するか、デフォルトで `_requested_tab()` を使う。

`export_rows` を次にする。

```python
            cached = _get_counts(case_id)
            key = _filter_cache_key(case_id)
            match = (
                cached["match_count"]
                if cached and cached.get("key") == key
                else None
            )
            data = rows_csv_bytes(conn, _session_conditions(case_id), match_count=match)
```

`docs/方式設計/A07-可用性・性能・運用監視.md` の 4.2 表に行を足す。R05 の分単位文言は触らない。

| フィルタ後の件数 | セッションに持ち、条件が同じあいだ COUNT しない |
| 日時フィルタ | `substr(time_abs,1,19)` の式索引 |
| 文字列の完全一致 | `log_values(identifier, str_value)` から `row_id` を取る |

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py tests/test_csv_export.py tests/test_filters.py tests/test_store.py -v`

Expected: PASS。

続けて全件。

Run: `python -m pytest`

Expected: PASS。

Docker 利用時は `docker compose up --build -d`。500万件案件は初回オープンで索引作成に時間がかかる。その後、`Time` 以降 ＋ `Request-Method` 等しい `POST` を適用し、取り込みタブへ切替がすぐ終わることを確認する。

- [ ] **Step 5: Commit**

スキップ。ユーザーが明示するまで commit しない。
