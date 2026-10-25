# 文字列の等しい／等しくない Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 文字列フィルタの先頭に「等しい」（`eq`）と「等しくない」（`ne`）を足し、完全一致とその補集合（未設定を含む）で絞り込めるようにする。

**Architecture:** エンジンは文字列分岐で `EXISTS (... col_s = ?)` を `eq`、その `NOT EXISTS` を `ne` にする。値は 1 文字列のまま（カンマ分割しない）。画面は文字列 `data-ops` の先頭に足す。数値・日時の既存 `eq` / `ne` は触らない。

**Tech Stack:** Python 3.12、sqlite3、Flask、Jinja2、pytest。追加依存なし。

## Global Constraints

- 仕様の正は `docs/superpowers/specs/2026-10-24-string-eq-ne-filter-design.md`
- 画面ラベルは `等しい` / `等しくない`、内部値は `eq` / `ne`
- 対象は文字列のみ（ログ列・カスタム列）。並びは文字列演算子の先頭
- 値は 1 テキスト。カンマは区切りにしない。前後空白は trim しない
- `eq` は完全一致。未設定は残さない
- `ne` は `eq` の補集合。未設定は残す
- 空文字の `eq` は値が空文字の行だけ。空文字の `ne` はその補集合
- 大文字小文字は区別する
- メッセージコードは増やさない
- テストはリポジトリルートで `python -m pytest`
- ユーザーが明示するまで git commit しない。各タスク末尾の Commit ステップはスキップする

## File Structure

```
src/logcase/filters.py                         # 文字列 eq / ne の SQL
tests/test_filters.py                                # 一致・未設定・カンマ・空文字
src/logcase/app.py                             # 許可演算子
src/logcase/templates/partials/macros.html     # data-ops の先頭
tests/test_app.py                                    # data-ops と適用の往復
docs/要件定義/R02-機能要件.md
docs/基本設計/WRK_D01-案件作業画面.md
```

`OP_LABELS`・`operator_options`・`app.js` の `eq` / `ne` ラベルは既存。サマリは `in` / `not_in` 以外が値表示なので、`filter_rail.html` / `list_panel.html` / `app.js` は変えない。`schema_panel.html` も変えない。

---

### Task 1: フィルタエンジンに文字列 eq / ne を足す

**Files:**
- Modify: `src/logcase/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: 既存 `Condition(identifier, source, field_type, op, value)`。`eq` / `ne` の `value` は `str`（`in` のような list ではない）
- Produces: `field_type == "string"` かつ `op == "eq"` のとき `EXISTS (... col_s = ?)`。`op == "ne"` のとき同じ述語の `NOT EXISTS`。数値・日時の `_one` 分岐は変更しない

- [ ] **Step 1: Write the failing tests**

`tests/test_filters.py` の末尾に追加する。`_line` はメソッド固定の GET なので、メソッド混在とクエリ付き行は `import_file` に生行を渡す。

```python
def test_string_eq_request_method(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "200"),
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "POST /b HTTP/1.1" 200 10\n',
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "HEAD /c HTTP/1.1" 200 10\n',
        ],
    )
    conds = [Condition("Request-Method", "log", "string", "eq", "GET")]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert rows[0]["Request-Method"] == "GET"
    ne = [Condition("Request-Method", "log", "string", "ne", "GET")]
    assert count_rows(conn, ne) == 2
    methods = {r["Request-Method"] for r in fetch_rows(conn, ne, after_row_id=0)}
    assert methods == {"POST", "HEAD"}
    conn.close()


def test_string_ne_keeps_unset_custom_values(tmp_path: Path):
    conn = _conn(tmp_path)
    add_custom_field(conn, "note")
    set_custom_value(conn, 2, "note", "bot")
    conds = [Condition("note", "custom", "string", "ne", "bot")]
    assert count_rows(conn, conds) == 2
    ids = [r["row_id"] for r in fetch_rows(conn, conds, after_row_id=0)]
    assert ids == [1, 3]
    conn.close()


def test_string_eq_keeps_comma_in_value(tmp_path: Path):
    conn = _conn(tmp_path)
    add_custom_field(conn, "note")
    set_custom_value(conn, 1, "note", "a,b")
    set_custom_value(conn, 2, "note", "a")
    conds = [Condition("note", "custom", "string", "eq", "a,b")]
    assert count_rows(conn, conds) == 1
    assert fetch_rows(conn, conds, after_row_id=0)[0]["row_id"] == 1
    conn.close()


def test_string_eq_empty_and_ne_complement(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "200"),
            '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /b?x=1 HTTP/1.1" 200 10\n',
        ],
    )
    eq = [Condition("Request-Query", "log", "string", "eq", "")]
    assert count_rows(conn, eq) == 1
    assert fetch_rows(conn, eq, after_row_id=0)[0]["Request-Path"] == "/a"
    ne = [Condition("Request-Query", "log", "string", "ne", "")]
    assert count_rows(conn, ne) == 1
    assert fetch_rows(conn, ne, after_row_id=0)[0]["Request-Query"] == "x=1"
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_filters.py::test_string_eq_request_method tests/test_filters.py::test_string_ne_keeps_unset_custom_values tests/test_filters.py::test_string_eq_keeps_comma_in_value tests/test_filters.py::test_string_eq_empty_and_ne_complement -v`

Expected: FAIL。`unknown op eq` または `unknown op ne` の `ValueError`。PASS ならテストが既存動作を見ているので直す。数値の `eq` / `ne` 分岐には入らない（`field_type` が `string`）。

- [ ] **Step 3: Write minimal implementation**

`src/logcase/filters.py` の `_one` で、数値分岐の直後・`if cond.op == "contains":` の直前に次を入れる。数値・日時の分岐は動かさない。

```python
    if cond.op == "eq":
        pred = f"{col_s} = ?"
        return exists.format(pred=pred), [key, cond.value]
    if cond.op == "ne":
        pred = f"{col_s} = ?"
        sql = exists.format(pred=pred)
        return f"NOT {sql}", [key, cond.value]
```

`eq` は `str_value` が入力と同一の行だけ。未設定は `str_value` が NULL か行が無いので EXISTS に入らない。`ne` はその否定なので未設定と他の値が残る。カンマはプレースホルダ 1 個の値そのもの。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_filters.py -v`

Expected: PASS（新規 4 件と既存。`test_contains_and_status` の数値 `eq` も残る）。

- [ ] **Step 5: Commit**

スキップ。ユーザーが明示するまで commit しない。

---

### Task 2: 画面・許可演算子・文書に出す

**Files:**
- Modify: `src/logcase/app.py`
- Modify: `src/logcase/templates/partials/macros.html`
- Modify: `tests/test_app.py`
- Modify: `docs/要件定義/R02-機能要件.md`
- Modify: `docs/基本設計/WRK_D01-案件作業画面.md`

**Interfaces:**
- Consumes: Task 1 の `Condition(..., field_type="string", op="eq"|"ne", value: str)`
- Produces: 文字列列の `data-ops` 先頭が `eq,ne`。POST の `value` は文字列のまま（`split(",")` しない）。サマリは値をそのまま出す

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py` の `test_filter_operator_options_follow_selected_field_type` の文字列期待を次に変える。数値・日時の行は変えない。

```python
    assert b'data-ops="eq,ne,contains,not_contains,starts,not_starts,in,not_in,gte,lte"' in rv.data
    assert b'data-ops="eq,ne,gt,ge,lt,le,unset"' in rv.data
    assert b'data-ops="eq,before,on_or_after,after,on_or_before,unset"' in rv.data
```

同じファイルに適用の往復テストを追加する。

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py::test_filter_operator_options_follow_selected_field_type tests/test_app.py::test_string_eq_filter_round_trip_keeps_comma tests/test_app.py::test_string_ne_filter_round_trip -v`

Expected: FAIL。`data-ops` が旧文字列、または `eq` / `ne` が文字列として許可されずセッションが空。PASS なら期待値を直す。

- [ ] **Step 3: Write minimal implementation**

`src/logcase/app.py` の `_valid_filter_op` の string 集合を次にする。`OP_LABELS` と `work_filter` の `split(",")` 判定は変えない。

```python
        "string": {"eq", "ne", "contains", "not_contains", "starts", "not_starts", "in", "not_in", "gte", "lte"},
```

`src/logcase/templates/partials/macros.html` の文字列 `data-ops` を 2 箇所とも次にする。

```
eq,ne,contains,not_contains,starts,not_starts,in,not_in,gte,lte
```

`operator_options` のラベル辞書は既に `eq` / `ne` があるので触らない。JS が `data-ops` 順で option を作り直す。

`docs/要件定義/R02-機能要件.md` の F-003 文字列演算子を次にする。処理概要の文は変えない。

```markdown
- **文字列演算子**: 等しい、等しくない、含む、含まない、始まる、始まらない、複数選択、複数選択以外、以上、以下。以上・以下は辞書順。大文字小文字は区別する。含む／始まると系統も区別する（想定）
```

`docs/基本設計/WRK_D01-案件作業画面.md` を次にする。

条件行の説明: `列、演算子、値。複数列は AND。複数選択は OR。等しくない／複数選択以外は指定値でない（未設定は残す）`

文字列演算子: `等しい、等しくない、含む、含まない、始まる、始まらない、複数選択、複数選択以外、以上、以下（辞書順、大文字小文字区別）`

フィルタ適用時の 2: `複数列は AND。1 列の複数選択は OR。等しくないは指定値と完全一致しない行（未設定を含む）。複数選択以外は指定値のいずれとも完全一致しない行（未設定を含む）`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py tests/test_filters.py -v`

Expected: PASS。

続けて全件。

Run: `python -m pytest`

Expected: PASS。

Docker で動かしている場合は静的ファイル反映のため `docker compose up --build -d` が必要。ブラウザで文字列列の演算子先頭が「等しい」「等しくない」であること、`Request-Method` 等しい `GET` で GET だけ残ること、等しくない `GET` でそれ以外が残ることを確認する。

- [ ] **Step 5: Commit**

スキップ。ユーザーが明示するまで commit しない。
