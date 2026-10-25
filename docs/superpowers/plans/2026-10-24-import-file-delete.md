# 取り込みファイル単位の削除 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取り込みタブから元ファイル名単位で、その行・値・取り込み履歴を確認のうえ消せるようにする。

**Architecture:** エンジンは `delete_imported_file(conn, filename) -> int`。行は `source_filename` で消し、値は `row_id` サブクエリで消す。画面は一括設定と同じ二段 POST（確認モーダル → `confirm=1`）。スキーマは変えない。

**Tech Stack:** Python 3.12、sqlite3、Flask、Jinja2、pytest。追加依存なし。

## Global Constraints

- 仕様の正は `docs/superpowers/specs/2026-10-24-import-file-delete-design.md`
- 削除キーは元ファイル名。取り込み履歴 ID では消さない
- 同じ名前の履歴が複数でもまとめて消す
- 確認は W_WRK_BIZ05（`{filename} の取り込み履歴と {row_count} 行を消します。取り消せません。`）。名前の再入力はしない
- 成功は S_WRK_OK03（取り込みファイルを削除しました。）
- 空または履歴に無いファイル名は何も消さずフラッシュなし
- 途中失敗はロールバックして既存 E_WRK_SYS01
- 案件が無ければ既存 E_CSE_EXS01
- スキーマ変更なし
- テストはリポジトリルートで `python -m pytest`
- ユーザーが明示するまで git commit しない。各タスク末尾の Commit ステップはスキップする

## File Structure

```
src/logcase/import_log.py                      # delete_imported_file
src/logcase/messages.py                        # W_WRK_BIZ05 / S_WRK_OK03
tests/test_import_log.py                             # エンジン
src/logcase/app.py                             # POST /cases/<id>/imports/delete
src/logcase/templates/partials/ingest_panel.html
src/logcase/templates/work.html                # 確認モーダル
src/logcase/templates/cases.html               # 注意文
tests/test_app.py                                    # 確認なし・成功・無い名前
docs/要件定義/R02-機能要件.md
docs/要件定義/R04-外部連携・データ.md
docs/要件定義/00-全体.md
docs/基本設計/WRK_D01-案件作業画面.md
docs/基本設計/03-データ構造設計.md
docs/基本設計/05-メッセージ一覧.md
```

---

### Task 1: エンジンで元ファイル名の行と履歴を消す

**Files:**
- Modify: `src/logcase/import_log.py`
- Modify: `src/logcase/messages.py`
- Test: `tests/test_import_log.py`

**Interfaces:**
- Consumes: 既存 `import_file` / `AppError` / `MSG["E_WRK_SYS01"]`
- Produces:
  - `delete_imported_file(conn: sqlite3.Connection, filename: str) -> int`
  - 戻り値は消した `log_rows` 件数
  - 空文字、または `import_files` にも `log_rows` にもその名前が無いときは 0（書き込みなし）
  - 成功時は同じ名前の `import_files` もすべて消す
  - `MSG["W_WRK_BIZ05"]` と `MSG["S_WRK_OK03"]`（このタスクでは関数から呼ばない。文言だけ置く）

- [ ] **Step 1: Write the failing tests**

`tests/test_import_log.py` の末尾に追加する。`LINE` と `COMMON` は既存。2 本目の行は `LINE` と内容を変える。

```python
from logcase.custom_fields import add_custom_field, set_custom_value
from logcase.import_log import delete_imported_file


LINE_B = '10.0.0.1 - - [10/Oct/2024:13:00:00 +0900] "GET /other HTTP/1.1" 404 9\n'


def test_delete_imported_file_removes_rows_values_and_history(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log.a", [LINE])
    import_file(conn, "access_log.b", [LINE_B])
    add_custom_field(conn, "note")
    set_custom_value(conn, 1, "note", "keep-a")
    set_custom_value(conn, 2, "note", "drop-b")

    deleted = delete_imported_file(conn, "access_log.b")

    assert deleted == 1
    names = [r[0] for r in conn.execute("SELECT source_filename FROM log_rows ORDER BY row_id")]
    assert names == ["access_log.a"]
    notes = {
        r["row_id"]: r["str_value"]
        for r in conn.execute("SELECT row_id, str_value FROM custom_values")
    }
    assert notes == {1: "keep-a"}
    vals = conn.execute(
        "SELECT COUNT(*) FROM log_values WHERE row_id = 2"
    ).fetchone()[0]
    assert vals == 0
    hist = [r[0] for r in conn.execute("SELECT source_filename FROM import_files")]
    assert hist == ["access_log.a"]
    conn.close()


def test_delete_imported_file_same_name_removes_all_history(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log", [LINE])
    import_file(conn, "access_log", [LINE_B])
    assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 2

    deleted = delete_imported_file(conn, "access_log")

    assert deleted == 2
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 0
    conn.close()


def test_delete_imported_file_keeps_row_owned_by_first_file(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log.a", [LINE])
    import_file(conn, "access_log.b", [LINE])

    deleted = delete_imported_file(conn, "access_log.b")

    assert deleted == 0
    row = conn.execute("SELECT source_filename FROM log_rows").fetchone()
    assert row["source_filename"] == "access_log.a"
    hist = [r[0] for r in conn.execute("SELECT source_filename FROM import_files")]
    assert hist == ["access_log.a"]
    conn.close()


def test_delete_imported_file_missing_or_empty_is_noop(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log", [LINE])

    assert delete_imported_file(conn, "") == 0
    assert delete_imported_file(conn, "no-such") == 0
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 1
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_import_log.py::test_delete_imported_file_removes_rows_values_and_history tests/test_import_log.py::test_delete_imported_file_same_name_removes_all_history tests/test_import_log.py::test_delete_imported_file_keeps_row_owned_by_first_file tests/test_import_log.py::test_delete_imported_file_missing_or_empty_is_noop -v`

Expected: FAIL。`delete_imported_file` が import できない。PASS ならテストが既存関数を見ているので直す。

- [ ] **Step 3: Write minimal implementation**

`src/logcase/messages.py` の `S_WRK_OK02` の直後へ次を入れる。

```python
    "W_WRK_BIZ05": "{filename} の取り込み履歴と {row_count} 行を消します。取り消せません。",
    "S_WRK_OK03": "取り込みファイルを削除しました。",
```

`src/logcase/import_log.py` の `import_file` の直後（`import_gzip_bytes` の前）へ次を入れる。

```python
def delete_imported_file(conn: sqlite3.Connection, filename: str) -> int:
    if not filename:
        return 0
    exists = conn.execute(
        "SELECT 1 FROM import_files WHERE source_filename = ? "
        "UNION ALL SELECT 1 FROM log_rows WHERE source_filename = ? LIMIT 1",
        (filename, filename),
    ).fetchone()
    if not exists:
        return 0
    try:
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM log_values WHERE row_id IN "
            "(SELECT row_id FROM log_rows WHERE source_filename = ?)",
            (filename,),
        )
        conn.execute(
            "DELETE FROM custom_values WHERE row_id IN "
            "(SELECT row_id FROM log_rows WHERE source_filename = ?)",
            (filename,),
        )
        cur = conn.execute(
            "DELETE FROM log_rows WHERE source_filename = ?",
            (filename,),
        )
        conn.execute(
            "DELETE FROM import_files WHERE source_filename = ?",
            (filename,),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise AppError("E_WRK_SYS01", MSG["E_WRK_SYS01"]) from exc
    return cur.rowcount
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_import_log.py -v`

Expected: PASS。

- [ ] **Step 5: Commit**

スキップ。ユーザーが明示するまで commit しない。

---

### Task 2: 画面・POST・文書

**Files:**
- Modify: `src/logcase/app.py`
- Modify: `src/logcase/templates/partials/ingest_panel.html`
- Modify: `src/logcase/templates/work.html`
- Modify: `src/logcase/templates/cases.html`
- Modify: `tests/test_app.py`
- Modify: `docs/要件定義/R02-機能要件.md`
- Modify: `docs/要件定義/R04-外部連携・データ.md`
- Modify: `docs/要件定義/00-全体.md`
- Modify: `docs/基本設計/WRK_D01-案件作業画面.md`
- Modify: `docs/基本設計/03-データ構造設計.md`
- Modify: `docs/基本設計/05-メッセージ一覧.md`

**Interfaces:**
- Consumes: Task 1 の `delete_imported_file(conn, filename) -> int`、`MSG["W_WRK_BIZ05"]`、`MSG["S_WRK_OK03"]`
- Produces: `POST /cases/<case_id>/imports/delete`。フォーム項目は `source_filename` と任意の `confirm`。`_work_context` に `import_delete_confirm: None | {filename, warning}`

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py` に追加する。取り込みは既存のプレーン取り込みテストと同じく `data=` とファイルタプルで行う。

```python
def test_import_delete_without_confirm_keeps_rows(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": [(io.BytesIO(LINE.encode()), "access_log")]},
        follow_redirects=True,
    )

    rv = client.post(
        f"/cases/{cid}/imports/delete",
        data={"source_filename": "access_log"},
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert rv.status_code == 200
    assert "取り込み履歴と" in html
    assert "1 行を消します" in html
    conn = open_case(tmp_path, cid)
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
    conn.close()


def test_import_delete_with_confirm_removes_file(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": [(io.BytesIO(LINE.encode()), "access_log")]},
        follow_redirects=True,
    )

    rv = client.post(
        f"/cases/{cid}/imports/delete",
        data={"source_filename": "access_log", "confirm": "1"},
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert "取り込みファイルを削除しました。" in html
    conn = open_case(tmp_path, cid)
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 0
    conn.close()


def test_import_delete_unknown_name_is_silent_noop(tmp_path: Path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    cid = _create_case(client, "X")
    client.post(
        f"/cases/{cid}/import",
        data={"files": [(io.BytesIO(LINE.encode()), "access_log")]},
        follow_redirects=True,
    )

    rv = client.post(
        f"/cases/{cid}/imports/delete",
        data={"source_filename": "no-such", "confirm": "1"},
        follow_redirects=True,
    )

    html = rv.data.decode("utf-8")
    assert "取り込みファイルを削除しました。" not in html
    conn = open_case(tmp_path, cid)
    assert conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0] == 1
    conn.close()
```

`LINE` が `test_app.py` に無い場合はファイル先頭の `COMMON` の下へ次を足す。

```python
LINE = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1234\n'
```

Flask のファイル投稿は環境によって `data={"files": (BytesIO, name)}` の形が必要なことがある。既存 `test_plain_import_preserves_unicode_line_separator` の投稿形に合わせる。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py::test_import_delete_without_confirm_keeps_rows tests/test_app.py::test_import_delete_with_confirm_removes_file tests/test_app.py::test_import_delete_unknown_name_is_silent_noop -v`

Expected: FAIL。404 または行が消えない。PASS なら期待を直す。

- [ ] **Step 3: Write minimal implementation**

`src/logcase/app.py` の `_work_context` 戻り値へ `"import_delete_confirm": None` を `bulk_confirm` の隣に足す。`from logcase.import_log` の import に `delete_imported_file` を足す。

`work_import` の直後へ次のルートを足す。

```python
    @app.post("/cases/<case_id>/imports/delete")
    def work_import_delete(case_id: str):
        filename = request.form.get("source_filename", "")
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
                flash(warning, "warn")
                context = _work_context(case_id)
                context["tab"] = "ingest"
                context["import_delete_confirm"] = {
                    "filename": filename,
                    "warning": warning,
                }
                return render_template("work.html", **context)
            delete_imported_file(conn, filename)
            app.config["AGGREGATE_CACHE"].pop(case_id, None)
            _flash_code("S_WRK_OK03")
        except AppError as exc:
            _flash_err(exc)
        finally:
            conn.close()
        return _work_redirect(case_id, "ingest")
```

確認分岐で `_work_context` が同じ `conn` を閉じたあとに再度開く。実装では確認時に `conn.close()` してから `_work_context` を呼ぶか、確認用に先に件数を取って `conn.close()` してから context を組む。二重 close しない。

推奨の形:

```python
        filename = request.form.get("source_filename", "")
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
                _flash_code("S_WRK_OK03")
        except AppError as exc:
            _flash_err(exc)
            return _work_redirect(case_id, "ingest")
        finally:
            conn.close()
        if warning is not None:
            flash(warning, "warn")
            context = _work_context(case_id)
            context["tab"] = "ingest"
            context["import_delete_confirm"] = {
                "filename": filename,
                "warning": warning,
            }
            return render_template("work.html", **context)
        return _work_redirect(case_id, "ingest")
```

`warning` は try の前に `warning = None` で初期化する。

`src/logcase/templates/partials/ingest_panel.html` の表ヘッダに空の操作列を足し、各行の末尾へ削除ボタンを足す。前提リストの「取り込んだファイルは単位では消せません（案件ごと削除のみ）。」を「取り込んだファイルは元ファイル名単位で消せます。同じ名前の履歴はまとめて消えます。」に替える。

ヘッダ:

```html
                  <th>元ファイル名</th>
                  <th class="r">読んだ</th>
                  <th class="r">入れた</th>
                  <th class="r">捨てた</th>
                  <th class="r">重複</th>
                  <th>取り込み日時</th>
                  <th></th>
```

行の末尾:

```html
                    <td>
                      <form method="post" action="{{ url_for('work_import_delete', case_id=case_id) }}">
                        <input type="hidden" name="tab" value="ingest">
                        <input type="hidden" name="source_filename" value="{{ f.source_filename }}">
                        <button type="submit" class="btn xs danger">削除</button>
                      </form>
                    </td>
```

`src/logcase/templates/work.html` の bulk モーダルのあとに確認モーダルを足す。`import_delete_confirm` が無いテンプレートでも落ちないよう `import_delete_confirm|default(none)` は使わず、context に必ずキーがある前提とする。

```html
<div class="scrim{% if import_delete_confirm %} is-open{% endif %}" data-close-overlay {% if not import_delete_confirm %}hidden{% endif %}></div>
<div class="modal{% if import_delete_confirm %} is-open{% endif %}" id="import-delete-modal" role="dialog" aria-modal="true" aria-label="取り込みファイルの削除" {% if not import_delete_confirm %}hidden{% endif %}>
  <form method="post" action="{{ url_for('work_import_delete', case_id=case_id) }}">
    <input type="hidden" name="tab" value="ingest">
    {% if import_delete_confirm %}
      <input type="hidden" name="confirm" value="1">
      <input type="hidden" name="source_filename" value="{{ import_delete_confirm.filename }}">
    {% endif %}
    <div class="modal-head">
      <h3>取り込みファイルを削除</h3>
    </div>
    <div class="modal-body">
      <p style="margin:0;font-size:12px;color:var(--ink-2);line-height:1.6">
        {% if import_delete_confirm %}{{ import_delete_confirm.warning }}{% endif %}
      </p>
    </div>
    <div class="modal-foot">
      <button type="button" class="btn" data-close-overlay>キャンセル</button>
      <button type="submit" class="btn danger">削除する</button>
    </div>
  </form>
</div>
{% if import_delete_confirm %}
  <script>document.body.classList.add("modal-open");</script>
{% endif %}
```

既存の bulk 用 scrim と二重になっても、確認時は ingest タブなので bulk は閉じたまま。scrim を 2 枚出すと重なるので、確認モーダルは既存 scrim を共有する。

共有する実装（こちらを採用）:

`work.html` の既存 scrim / `modal-open` を次のように変える。

```html
<div class="scrim{% if bulk_confirm or import_delete_confirm %} is-open{% endif %}" data-close-overlay {% if not bulk_confirm and not import_delete_confirm %}hidden{% endif %}></div>
```

bulk モーダルの条件はそのまま。その直後に import-delete モーダルを置く（scrim は増やさない）。末尾の script を次にする。

```html
{% if bulk_confirm or import_delete_confirm %}
  <script>document.body.classList.add("modal-open");</script>
{% endif %}
```

`src/logcase/templates/cases.html` の注意文を次にする。

```
行単位では削除できません。取り込みファイルは作業画面の取り込みタブから消せます。バックアップ機能もありません。
```

文書:

`docs/要件定義/R02-機能要件.md`

- 機能表 F-010 の備考を `行単位の削除は対象外` にする
- F-010 の次へ行を足す: `| F-011 | 取り込みファイル削除 | 元ファイル名の行と履歴を破棄 | Must | 同じ名前の履歴はまとめて消す。取り消せない |`
- F-010 処理概要から「取り込み済みファイル単位・」を除く
- F-010 のあとに F-011 詳細を足す:

```markdown
##### F-011 取り込みファイル削除

- **目的**: 誤って取り込んだファイルの行を、案件を作り直さずに除く
- **処理概要**: 取り込みタブで元ファイル名を指定し、確認のあと、その名前の行・カスタム値・取り込み履歴を破棄する。同じ名前の履歴はまとめて消す。取り消せない。行単位では消せない
- **関連**: R03 取り込み、R04、F-002、F-010
```

`docs/要件定義/R04-外部連携・データ.md` の取り込みファイル情報の備考を `作業画面から元ファイル名単位で削除できる` にする。

`docs/要件定義/00-全体.md`

- 対象外リストの「取り込み済みファイル単位の削除、行単位の削除」を「行単位の削除」にする
- AC-011 の「ファイル単位では消せない」を「行単位では消せない。取り込みファイルは作業画面から消せる」にする

`docs/基本設計/WRK_D01-案件作業画面.md`

- 取り込み済み一覧の説明に「各行に削除」を足す
- 取り込み前提の「取り込んだファイルは単位では消せません」を改める
- アクションに「取り込みファイル削除押下時」を足す:

```
1. 元ファイル名必須。空または履歴に無ければ何もしない
2. confirm が無ければ W_WRK_BIZ05（ファイル名と行数）を出して取り込みタブに留まる
3. confirm=1 ならその元ファイル名の行・値・同じ名前の履歴を 1 トランザクションで消す。失敗は E_WRK_SYS01
4. 成功は S_WRK_OK03。集計キャッシュを捨て、取り込みタブを取り直す
```

`docs/基本設計/03-データ構造設計.md` の「ファイル単位では削除しない。同じ元ファイル名を再取り込みした場合は別レコード。」を「同じ元ファイル名を再取り込みした場合は別レコード。削除は元ファイル名単位で、同じ名前の履歴と行をまとめて消す。」にする。

`docs/基本設計/05-メッセージ一覧.md` の調査行へ次を足す。

```
| 調査 | W_WRK_BIZ05 | 取り込みファイル削除確認 | 元ファイル名 | 行数確認 | {filename} の取り込み履歴と {row_count} 行を消します。取り消せません。 |
| 調査 | S_WRK_OK03 | 取り込みファイル削除 | — | 削除成功 | 取り込みファイルを削除しました。 |
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py tests/test_import_log.py -v`

Expected: PASS。

続けて全件。

Run: `python -m pytest`

Expected: PASS。

Docker 利用時は `docker compose up --build -d` のあと、取り込みタブで削除 → 確認モーダル → 行と履歴が消えることを確認する。

- [ ] **Step 5: Commit**

スキップ。ユーザーが明示するまで commit しない。
