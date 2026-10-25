# 複数選択以外（not_in） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 文字列フィルタに演算子「複数選択以外」（`not_in`）を足し、指定値のどれかと完全一致する行を除いて残り（未設定を含む）を残す。

**Architecture:** エンジンは既存 `in` の `EXISTS ... col IN (...)` を `NOT EXISTS` にした補集合。フォームは `in` と同じカンマ区切り。ラベルと演算子一覧だけを画面側で足す。案件スキーマは変えない。

**Tech Stack:** Python 3.12、sqlite3、Flask、Jinja2、pytest。追加依存なし。

## Global Constraints

- 仕様の正は `docs/superpowers/specs/2026-10-23-not-in-filter-design.md`
- 画面ラベルは `複数選択以外`、内部値は `not_in`
- 対象は文字列のみ。並びは複数選択の直後
- 入力は `in` と同じカンマ区切り。前後空白は trim しない
- 未設定行は残す（`in` の補集合）
- エンジンへ空リストを渡した `not_in` は全件（空の `in` は 0 件）
- メッセージコードは増やさない
- テストはリポジトリルートで `python -m pytest`
- ユーザーが明示するまで git commit しない。各タスク末尾の Commit ステップはスキップする

## File Structure

```
src/logcase/filters.py                         # not_in SQL
tests/test_filters.py                                # 除外・未設定・空リスト
src/logcase/app.py                             # 許可演算子・ラベル・カンマ分割
src/logcase/templates/partials/macros.html     # data-ops と option ラベル
src/logcase/templates/partials/filter_rail.html
src/logcase/templates/partials/list_panel.html
src/logcase/static/app.js                      # ラベルとサマリ件数
tests/test_app.py                                    # data-ops と適用の往復
docs/要件定義/R02-機能要件.md
docs/基本設計/WRK_D01-案件作業画面.md
```

`schema_panel.html` は `op_labels` と list の `join` ですでに足りるので、ラベル追加以外の変更はしない。

---

### Task 1: フィルタエンジンに not_in を足す

**Files:**
- Modify: `src/logcase/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: 既存 `Condition(identifier, source, field_type, op, value)`。`in` と同じく `value` は `list[str]`
- Produces: `op == "not_in"` のとき、同じ列の `IN` 述語を `NOT EXISTS` にした WHERE。未設定・他の値・値行が無い行が残る

- [ ] **Step 1: Write the failing tests**

`tests/test_filters.py` の末尾に追加する。`_line` はメソッド固定の GET なので、メソッド混在は `import_file` に生行を渡す。

```python
def test_not_in_excludes_listed_request_methods(tmp_path: Path):
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
    conds = [
        Condition("Request-Method", "log", "string", "not_in", ["GET", "HEAD"]),
    ]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert rows[0]["Request-Method"] == "POST"
    conn.close()


def test_not_in_keeps_unset_custom_values(tmp_path: Path):
    conn = _conn(tmp_path)
    add_custom_field(conn, "note")
    set_custom_value(conn, 2, "note", "bot")
    conds = [Condition("note", "custom", "string", "not_in", ["bot"])]
    assert count_rows(conn, conds) == 2
    ids = [r["row_id"] for r in fetch_rows(conn, conds, after_row_id=0)]
    assert ids == [1, 3]
    conn.close()


def test_not_in_empty_list_matches_all(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Request-Method", "log", "string", "not_in", [])]
    assert count_rows(conn, conds) == 3
    conn.close()
```

空リストはエンジンに `[]` を渡したとき。フォーム空文字の `"".split(",") == [""]` は `in` と同じ経路なので、このタスクでは扱わない。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_filters.py::test_not_in_excludes_listed_request_methods tests/test_filters.py::test_not_in_keeps_unset_custom_values tests/test_filters.py::test_not_in_empty_list_matches_all -v`

Expected: FAIL。`unknown op not_in` の `ValueError`。PASS ならテストが既存動作を見ているので直す。

- [ ] **Step 3: Write minimal implementation**

`src/logcase/filters.py` の `if cond.op == "in":` ブロックを次に置き換える。

```python
    if cond.op in {"in", "not_in"}:
        values = list(cond.value or [])
        placeholders = ",".join("?" * len(values)) or "NULL"
        pred = f"{col_s} IN ({placeholders})"
        sql = exists.format(pred=pred)
        params = [key, *values]
        if cond.op == "not_in":
            return f"NOT {sql}", params
        return sql, params
```

`NOT EXISTS (...)` になる。`IN` に一致する行だけが落ち、未設定（`str_value` が NULL）と他の値は残る。空リストは既存どおり `IN (NULL)` なので `in` は 0 件、`not_in` は全件。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_filters.py -v`

Expected: PASS（新規 3 件と既存）。

- [ ] **Step 5: Commit**

スキップ。ユーザーが明示するまで commit しない。

---

### Task 2: 画面・適用経路・文書に演算子を出す

**Files:**
- Modify: `src/logcase/app.py`
- Modify: `src/logcase/templates/partials/macros.html`
- Modify: `src/logcase/templates/partials/filter_rail.html`
- Modify: `src/logcase/templates/partials/list_panel.html`
- Modify: `src/logcase/static/app.js`
- Modify: `tests/test_app.py`
- Modify: `docs/要件定義/R02-機能要件.md`
- Modify: `docs/基本設計/WRK_D01-案件作業画面.md`

**Interfaces:**
- Consumes: Task 1 の `Condition(..., op="not_in", value: list[str])`
- Produces: 文字列列の演算子に `not_in`。POST の値は `in` と同じ `value.split(",")`。サマリは `[N 件]`

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py` の `test_filter_operator_options_follow_selected_field_type` の期待を次に変える。

```python
    assert b'data-ops="contains,not_contains,starts,not_starts,in,not_in,gte,lte"' in rv.data
```

同じファイルに適用の往復テストを追加する。

```python
def test_not_in_filter_round_trip(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")

    rv = client.post(
        f"/cases/{cid}/filter",
        data={
            "identifier": "log:string:RemoteHost",
            "op": "not_in",
            "value": "10.0.0.1,10.0.0.2",
        },
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert rv.status_code == 200
    assert "複数選択以外" in html
    assert "10.0.0.1,10.0.0.2" in html
    with client.session_transaction() as sess:
        spec = sess["filters_by_case"][cid]
        assert spec[0]["op"] == "not_in"
        assert spec[0]["value"] == ["10.0.0.1", "10.0.0.2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py::test_filter_operator_options_follow_selected_field_type tests/test_app.py::test_not_in_filter_round_trip -v`

Expected: FAIL。`data-ops` が旧文字列、または `not_in` が許可されずセッションが空。PASS なら期待値を直す。

- [ ] **Step 3: Write minimal implementation**

`src/logcase/app.py` の `OP_LABELS` で `"in": "複数選択"` の直後へ `"not_in": "複数選択以外"` を入れる。

`_valid_filter_op` の string 集合を次にする。

```python
        "string": {"contains", "not_contains", "starts", "not_starts", "in", "not_in", "gte", "lte"},
```

`work_filter` の value 組み立てを次にする。

```python
                    "value": value.split(",") if operator in {"in", "not_in"} else value,
```

`src/logcase/templates/partials/macros.html` の文字列 `data-ops` を 2 箇所とも次にする。

```
contains,not_contains,starts,not_starts,in,not_in,gte,lte
```

同じファイルの `operator_options` ラベルで `"in": "複数選択"` の直後へ `"not_in": "複数選択以外"` を入れる。

`src/logcase/templates/partials/filter_rail.html` の件数表示を `in` と `not_in` の両方にする。

```jinja
                  {% if spec.op in ("in", "not_in") %} [{{ spec.value_text.split(",")|length }} 件]
```

`src/logcase/templates/partials/list_panel.html` も同じ。

```jinja
          <span class="t">{{ spec.identifier }} {{ op_labels.get(spec.op, spec.op) }} {% if spec.op in ("in", "not_in") %}{{ spec.value_text.split(",")|length }}件{% else %}{{ spec.value_text }}{% endif %}</span>
```

`src/logcase/static/app.js` の `operatorLabels` で `in: "複数選択"` の直後へ `not_in: "複数選択以外"` を入れる。サマリの件数判定を次にする。

```javascript
      const shown = (opId === "in" || opId === "not_in")
        ? "[" + String((val && val.value) || "").split(",").filter(Boolean).length + " 件]"
        : '"' + ((val && val.value) || "—") + '"';
```

`docs/要件定義/R02-機能要件.md` の F-003 を次にする。

```markdown
- **処理概要**: 条件はフィールドごと（カスタムフィールドを含む）。複数列は AND。1 列の複数選択は OR、複数選択以外はその否定（未設定は残す）。適用で一致件数を出す。解除で全件に戻る。数値・日時に変換できない条件値では実行しない
- **文字列演算子**: 含む、含まない、始まる、始まらない、複数選択、複数選択以外、以上、以下。以上・以下は辞書順。大文字小文字は区別する。含む／始まると系統も区別する（想定）
```

`docs/基本設計/WRK_D01-案件作業画面.md` の条件行説明・文字列演算子・フィルタ適用時を次にする。

条件行の説明: `列、演算子、値。複数列は AND。複数選択は OR。複数選択以外は指定値のどれでもない（未設定は残す）`

文字列演算子: `含む、含まない、始まる、始まらない、複数選択、複数選択以外、以上、以下（辞書順、大文字小文字区別）`

フィルタ適用時の 2: `複数列は AND。1 列の複数選択は OR。複数選択以外は指定値のいずれとも完全一致しない行（未設定を含む）`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py tests/test_filters.py -v`

Expected: PASS。

続けて全件。

Run: `python -m pytest`

Expected: PASS。

Docker で動かしている場合は静的ファイル反映のため `docker compose up --build -d` が必要。ブラウザで文字列列の演算子に「複数選択以外」が出ること、`GET,HEAD` を入れて適用すると GET/HEAD 以外が残ることを確認する。

- [ ] **Step 5: Commit**

スキップ。ユーザーが明示するまで commit しない。
