# Request 分解列 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LogFormat に引数なし `%r`（識別子 `Request`）がある案件で、取り込み時に Request-Method / Request-Path / Request-Query / Request-Protocol を通常のログ列として持つ。

**Architecture:** `compile_logformat` が Request の直後へ分解列を挿入する（行マッチ用捕捉は増やさない）。`parse_line` は LogFormat 由来の捕捉だけを読み、Request の文字列を分解して値を足す。`create_case` / `import_file` / フィルタ / CSV は既存の `log_fields` / `log_values` 経路のまま。既存案件は変換しない。

**Tech Stack:** Python 3.12、sqlite3、pytest。追加依存なし。

## Global Constraints

- 仕様の正は `docs/superpowers/specs/2026-10-23-request-decompose-design.md`
- パースアルゴリズムの正は更新後の `docs/基本設計/CSE_C01-LogFormat解析.md`
- メッセージ文言は増やさない（重複は既存 E_CSE_DUP01）
- 分解列の指定子は `derived:%r`、key は `derived`、型は文字列、`derived=True`
- 対象は引数なし `%r` のみ。`%{名前}r` や識別子が Request のヘッダーは分解しない
- 既存案件の自動変換はしない
- テストはリポジトリルートで `python -m pytest`
- ユーザーが明示するまで git commit しない。各タスク末尾の Commit ステップはスキップする

## File Structure

```
src/logcase/logformat.py          # FieldDef.derived / compile 挿入 / split_request / parse_line
tests/test_logformat_compile.py         # 列順・非対象・衝突
tests/test_logformat_parse.py           # 分解例
tests/test_store.py                     # 案件登録時の log_fields 順
tests/test_import_log.py                # 取り込みで値が入る
tests/test_filters.py                   # Request-Path で絞れる
tests/test_csv_export.py                # ヘッダー順とセル位置
docs/基本設計/CSE_C01-LogFormat解析.md
docs/基本設計/03-データ構造設計.md
```

`import_log.py` / `store.py` / `filters.py` / `csv_export.py` / 画面は、列定義と値が既存経路に乗るためコード変更しない。

---

### Task 1: 案件登録時に分解列を定義する

**Files:**
- Modify: `src/logcase/logformat.py`
- Modify: `tests/test_logformat_compile.py`
- Modify: `tests/test_store.py`
- Modify: `tests/test_csv_export.py`
- Modify: `docs/基本設計/CSE_C01-LogFormat解析.md`（定義生成）
- Modify: `docs/基本設計/03-データ構造設計.md`

**Interfaces:**
- Consumes: 既存 `FieldDef` / `compile_logformat` / `parse_line`
- Produces:
  - `REQUEST_DERIVED_IDS: tuple[str, ...] = ("Request-Method", "Request-Path", "Request-Query", "Request-Protocol")`
  - `FieldDef(..., derived: bool = False)`
  - `%r` ありなら `compiled.fields` の Request 直後に 4 列。`order` は 0 始まりで振り直し
  - `parse_line` は `not field.derived` の列だけで捕捉グループ数を照合する（このタスクでは分解値はまだ足さない）

- [ ] **Step 1: Write the failing tests**

`tests/test_logformat_compile.py` の `test_common_format_field_order` を次の期待に変える。`test_named_header_identifier_preserves_case` のあとにテストを追加する。

```python
def test_common_format_field_order():
    compiled = compile_logformat('%h %l %u %t \\"%r\\" %>s %b')
    ids = [f.identifier for f in compiled.fields]
    assert ids == [
        "RemoteHost",
        "RemoteLogName",
        "RemoteUser",
        "Time",
        "Request",
        "Request-Method",
        "Request-Path",
        "Request-Query",
        "Request-Protocol",
        "Status",
        "Bytes",
    ]
    time_f = compiled.fields[3]
    assert time_f.field_type == "datetime"
    assert time_f.time_axis is True
    status = compiled.fields[9]
    assert status.key == "s"
    assert status.identifier == "Status"
    derived = compiled.fields[5]
    assert derived.identifier == "Request-Method"
    assert derived.field_type == "string"
    assert derived.specifier == "derived:%r"
    assert derived.key == "derived"
    assert derived.derived is True
    assert compiled.fields[4].derived is False


def test_no_request_field_has_no_derived_columns():
    compiled = compile_logformat("%h %t %s")
    assert [f.identifier for f in compiled.fields] == [
        "RemoteHost",
        "Time",
        "Status",
    ]


def test_named_request_header_is_not_decomposed():
    compiled = compile_logformat('%h \\"%{Request}i\\"')
    assert [f.identifier for f in compiled.fields] == ["RemoteHost", "Request"]


def test_derived_identifier_collision_raises_dup01():
    with pytest.raises(LogFormatError) as ei:
        compile_logformat('%r \\"%{Request-Path}i\\"')
    assert ei.value.code == "E_CSE_DUP01"
```

`tests/test_store.py` の `test_log_fields_persisted` の識別子リストを compile と同じ 11 個に合わせる。

`tests/test_csv_export.py` のヘッダーとセル位置を分解列込みに変える。この時点では分解値は空でもよい。

```python
LOG_HEADERS = [
    "RemoteHost",
    "RemoteLogName",
    "RemoteUser",
    "Time",
    "Request",
    "Request-Method",
    "Request-Path",
    "Request-Query",
    "Request-Protocol",
    "Status",
    "Bytes",
]
```

`test_rows_csv_columns_custom_and_unset` を次のように直す。

```python
    assert rows[0][3:14] == LOG_HEADERS
    assert rows[0][14:] == ["機能名", "担当"]
    assert rows[1][6] == "10/Oct/2024:12:00:00 +0900"
    assert rows[1][12] == "200"
    assert rows[1][13] == "1234"
    assert rows[1][14] == "検索"
    assert rows[1][15] == ""
```

`test_rows_csv_respects_conditions` の `rows[1][7]`（Request）はそのまま。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_logformat_compile.py::test_common_format_field_order tests/test_logformat_compile.py::test_no_request_field_has_no_derived_columns tests/test_logformat_compile.py::test_named_request_header_is_not_decomposed tests/test_logformat_compile.py::test_derived_identifier_collision_raises_dup01 -v`

Expected: FAIL（4 列が無い、またはテスト未定義）

- [ ] **Step 3: Implement compile insertion and capture-only parse**

`src/logcase/logformat.py`:

`FieldDef` の末尾に `derived: bool = False` を足す。モジュール定数を足す。

```python
REQUEST_DERIVED_IDS = (
    "Request-Method",
    "Request-Path",
    "Request-Query",
    "Request-Protocol",
)
```

`compile_logformat` の指定子ループの `FieldDef(...)` に `derived=False` を明示してよい。パターン compile の**前**に分解列を挿入する（パターン文字列は触らない）。

```python
        pattern_src = pattern_src.replace(specifier, pat, 1)
    if any(f.key == "r" and f.identifier == "Request" and not f.derived for f in fields):
        for ident in REQUEST_DERIVED_IDS:
            if ident in seen:
                raise LogFormatError(
                    "E_CSE_DUP01", "同じフィールド名が重複しているため登録できません。"
                )
            seen.add(ident)
        expanded: list[FieldDef] = []
        for field in fields:
            expanded.append(field)
            if field.key == "r" and field.identifier == "Request" and not field.derived:
                for ident in REQUEST_DERIVED_IDS:
                    expanded.append(
                        FieldDef(
                            identifier=ident,
                            field_type="string",
                            specifier="derived:%r",
                            order=0,
                            time_axis=False,
                            key="derived",
                            derived=True,
                        )
                    )
        fields = [
            FieldDef(
                identifier=f.identifier,
                field_type=f.field_type,
                specifier=f.specifier,
                order=i,
                time_axis=f.time_axis,
                key=f.key,
                derived=f.derived,
            )
            for i, f in enumerate(expanded)
        ]
    try:
        compiled = re.compile(pattern_src, re.IGNORECASE)
```

`parse_line` の照合を捕捉列だけにする。これをしないと `%r` 案件の全行が破棄される。

```python
    capture_fields = [f for f in compiled.fields if not f.derived]
    match = compiled.pattern.match(escaped)
    if match is None or len(match.groups()) != len(capture_fields):
        return None
    values: list[ParsedValue] = []
    time_display = None
    time_abs = None
    time_offset = None
    for i, field in enumerate(capture_fields):
        raw = match.group(i + 1)
        # 以降は現行どおり field を使う
```

- [ ] **Step 4: Update basic design docs for field insertion**

`docs/基本設計/CSE_C01-LogFormat解析.md` の「1. 定義の生成」ステップ 8 のあと、10 の前に次を入れる（番号を振り直す）。

- 捕捉パターンは LogFormat 指定子の分だけ。分解列は捕捉に含めない
- 引数なし `%r`（識別子 `Request`）があるとき、識別子 `Request-Method` / `Request-Path` / `Request-Query` / `Request-Protocol` を Request の直後へ挿入する。型は文字列。指定子は `derived:%r`
- 挿入する識別子が既出なら E_CSE_DUP01
- 表示順は挿入後に左から振り直す
- `%{名前}r` や `%{Request}i` では挿入しない

「2. 行の分割」ステップ 4 を「捕捉グループ数と **LogFormat 由来（分解列を除く）** フィールド数が一致しなければ破棄」に直す。

`docs/基本設計/03-データ構造設計.md` のログフィールド定義:

- 表示順: 「LogFormat 上の出現順。Request 分解列があるときは Request の直後に挿入し、以降をずらす」
- フィールド識別子: 例に `Request-Path` を足す
- 指定子: 分解列は `derived:%r`

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_logformat_compile.py tests/test_logformat_parse.py tests/test_store.py tests/test_import_log.py tests/test_csv_export.py tests/test_filters.py -v`

Expected: PASS。分解値のテストはこのタスクではまだ無い。

- [ ] **Step 6: Commit**

スキップする。

---

### Task 2: Request 文字列を 4 列に分解する

**Files:**
- Modify: `src/logcase/logformat.py`
- Modify: `tests/test_logformat_parse.py`
- Modify: `docs/基本設計/CSE_C01-LogFormat解析.md`（行分割の分解手順）

**Interfaces:**
- Consumes: Task 1 の `REQUEST_DERIVED_IDS` と `FieldDef.derived`
- Produces: `split_request(raw: str) -> list[ParsedValue]`（常に 4 要素、識別子順は `REQUEST_DERIVED_IDS`）。`parse_line` は Request の `str_value` を渡し、戻りを `values` へ Request の直後に挿入する

- [ ] **Step 1: Write the failing tests**

`tests/test_logformat_parse.py` に追加する。

```python
def _by_id(parsed):
    return {v.identifier: v for v in parsed.values}


def test_split_request_common_line():
    c = compile_logformat(COMMON)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /search?q=foo&p=1 HTTP/1.1" 200 1234'
    parsed = parse_line(c, line)
    assert parsed is not None
    by_id = _by_id(parsed)
    assert by_id["Request"].str_value == "GET /search?q=foo&p=1 HTTP/1.1"
    assert by_id["Request-Method"].str_value == "GET"
    assert by_id["Request-Path"].str_value == "/search"
    assert by_id["Request-Query"].str_value == "q=foo&p=1"
    assert by_id["Request-Protocol"].str_value == "HTTP/1.1"
    assert by_id["Request-Query"].is_unset is False


def test_split_request_empty_query_is_empty_string():
    c = compile_logformat(COMMON)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1'
    by_id = _by_id(parse_line(c, line))
    assert by_id["Request-Path"].str_value == "/index"
    assert by_id["Request-Query"].is_unset is False
    assert by_id["Request-Query"].str_value == ""
    assert by_id["Request-Protocol"].str_value == "HTTP/1.1"


def test_split_request_http09_protocol_unset():
    from logcase.logformat import split_request

    values = {v.identifier: v for v in split_request("GET /index")}
    assert values["Request-Method"].str_value == "GET"
    assert values["Request-Path"].str_value == "/index"
    assert values["Request-Query"].str_value == ""
    assert values["Request-Protocol"].is_unset is True
    assert values["Request-Protocol"].str_value is None


def test_split_request_dash_all_unset():
    from logcase.logformat import split_request

    values = split_request("-")
    assert [v.identifier for v in values] == [
        "Request-Method",
        "Request-Path",
        "Request-Query",
        "Request-Protocol",
    ]
    assert all(v.is_unset and v.str_value is None for v in values)


def test_parse_dash_request_keeps_request_and_unsets_derived():
    c = compile_logformat(COMMON)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "-" 400 0'
    parsed = parse_line(c, line)
    assert parsed is not None
    by_id = _by_id(parsed)
    assert by_id["Request"].str_value == "-"
    assert by_id["Request"].is_unset is False
    assert by_id["Request-Method"].is_unset is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_logformat_parse.py::test_split_request_common_line tests/test_logformat_parse.py::test_split_request_empty_query_is_empty_string tests/test_logformat_parse.py::test_split_request_http09_protocol_unset tests/test_logformat_parse.py::test_split_request_dash_all_unset tests/test_logformat_parse.py::test_parse_dash_request_keeps_request_and_unsets_derived -v`

Expected: FAIL（`split_request` 未定義、または分解値が無い）

- [ ] **Step 3: Implement split_request and parse_line insertion**

`src/logcase/logformat.py` に追加する。

```python
def _unset_derived() -> list[ParsedValue]:
    return [ParsedValue(ident, True, None, None) for ident in REQUEST_DERIVED_IDS]


def split_request(raw: str) -> list[ParsedValue]:
    if raw == "-":
        return _unset_derived()
    parts = raw.split()
    if not parts:
        return _unset_derived()
    method = parts[0]
    rest = parts[1:]
    protocol: str | None = None
    if rest and rest[-1].startswith("HTTP/"):
        protocol = rest[-1]
        target = " ".join(rest[:-1])
    else:
        target = " ".join(rest)
    if "?" in target:
        path, query = target.split("?", 1)
    else:
        path, query = target, ""
    values = [
        ParsedValue("Request-Method", False, method, None),
        ParsedValue("Request-Path", False, path, None),
        ParsedValue("Request-Query", False, query, None),
    ]
    if protocol is None:
        values.append(ParsedValue("Request-Protocol", True, None, None))
    else:
        values.append(ParsedValue("Request-Protocol", False, protocol, None))
    return values
```

`parse_line` で捕捉列を処理したあと、Request の値を分解して `values` の Request の直後へ挿入する。

```python
        else:
            values.append(ParsedValue(field.identifier, False, raw, None))
    request = next((v for v in values if v.identifier == "Request"), None)
    if request is not None and any(f.derived for f in compiled.fields):
        derived_values = split_request(request.str_value or "")
        insert_at = next(
            i for i, v in enumerate(values) if v.identifier == "Request"
        ) + 1
        values[insert_at:insert_at] = derived_values
    return ParsedLine(values, time_display, time_abs, time_offset)
```

`Request` が `-` のとき現行どおり `str_value` は `"-"` のまま（文字列の `-` はそのまま）。分解側だけ未設定。

- [ ] **Step 4: Document split steps in CSE_C01**

`docs/基本設計/CSE_C01-LogFormat解析.md` の「2. 行の分割」の型変換のあとに分解手順を足す。仕様書 4 節と同じ内容にする。

- Request が `-` なら 4 列とも未設定
- 空白区切りの先頭トークンが Method。末尾が `HTTP/` で始まるなら Protocol、残りがリクエストターゲット
- ターゲットを最初の `?` で分ける。前が Path、後が Query（`?` は含めない）。`?` が無ければ Query は空文字（未設定ではない）
- `GET /index` のように Protocol が無いときは Protocol のみ未設定
- 絶対 URI は URL として再解釈しない（`GET http://host/foo?x=1 HTTP/1.1` の Path は `http://host/foo`）
- 分解不能な断片だけ未設定。行は破棄しない
- 例表を CSE_C01 に置く（仕様書 4 節の 4 行）

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_logformat_parse.py tests/test_logformat_compile.py tests/test_import_log.py tests/test_csv_export.py -v`

Expected: PASS。CSV の分解セルはこの時点で値が入る（import が `parsed.values` を書くため）。`test_rows_csv_columns_custom_and_unset` は分解セルをまだ断言しなくてよい。

- [ ] **Step 6: Commit**

スキップする。

---

### Task 3: 取り込み・フィルタ・CSV で分解列を使うことを固定する

**Files:**
- Modify: `tests/test_import_log.py`
- Modify: `tests/test_filters.py`
- Modify: `tests/test_csv_export.py`

**Interfaces:**
- Consumes: Task 2 の `parse_line` が分解値を含むこと、Task 1 の `log_fields` 順
- Produces: 追加のアプリコードなし。回帰テストだけ

- [ ] **Step 1: Write the failing tests**

`tests/test_import_log.py` に追加する。

```python
def test_import_persists_request_parts(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /search?q=foo HTTP/1.1" 200 1\n'
    import_file(conn, "access_log", [line])
    rows = {
        r["identifier"]: r
        for r in conn.execute(
            "SELECT identifier, is_unset, str_value FROM log_values "
            "WHERE identifier LIKE 'Request%'"
        )
    }
    assert rows["Request"]["str_value"] == "GET /search?q=foo HTTP/1.1"
    assert rows["Request-Method"]["str_value"] == "GET"
    assert rows["Request-Path"]["str_value"] == "/search"
    assert rows["Request-Query"]["str_value"] == "q=foo"
    assert rows["Request-Protocol"]["str_value"] == "HTTP/1.1"
    assert rows["Request-Query"]["is_unset"] == 0
    conn.close()
```

`tests/test_filters.py` に追加する。

```python
def test_filter_eq_request_path(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Request-Path", "log", "string", "eq", "/api")]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert rows[0]["Request-Path"] == "/api"
    assert rows[0]["Request-Query"] == ""
    conn.close()
```

`tests/test_csv_export.py` の `test_rows_csv_columns_custom_and_unset` に分解値の断言を足す（1 行目は `GET /index HTTP/1.1`）。

```python
    assert rows[1][7] == "GET /index HTTP/1.1"
    assert rows[1][8] == "GET"
    assert rows[1][9] == "/index"
    assert rows[1][10] == ""
    assert rows[1][11] == "HTTP/1.1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_import_log.py::test_import_persists_request_parts tests/test_filters.py::test_filter_eq_request_path tests/test_csv_export.py::test_rows_csv_columns_custom_and_unset -v`

Expected: Task 2 まで完了していれば PASS になり得る。FAIL なら Task 2 の挿入漏れ（Request 直後に値が無い、import が derived を書いていない）を直す。新規コードが必要なら `parse_line` の挿入位置だけを直す。`import_log.py` は変えない。

- [ ] **Step 3: Fix only if tests fail**

FAIL の原因が「`parsed.values` に分解列が無い」なら Task 2 の挿入を直す。「`log_fields` に無い」なら Task 1 を直す。値の SELECT が 0 件なら `import_file` のループが identifier を落としていないか確認する（現行は `parsed.values` 全件 INSERT、datetime だけ skip）。

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -v`

Expected: PASS（全件）

- [ ] **Step 5: Commit**

スキップする。
