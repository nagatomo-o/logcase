# DuckDB 一括取り込み

日付: 2026-10-25  
承認済み。案件は DuckDB のみ。1行 INSERT と行ごとの重複 SELECT はやめる。

## エンジン

- 新規案件は `{id}.duckdb` だけ。`.sqlite` は作らない。
- 一覧・オープンは `.duckdb`。残存 `.sqlite` は起動時に一度だけ変換し、以降は更新日時で再変換しない。
- SQLite フォールバックはしない。テストも Docker も DuckDB。

## 取り込み

1. Python で LogFormat 解析（空行・解釈不能は破棄）。
2. 通った行を一時テーブルへまとめて載せる。
3. ファイル内先勝ちと案件内 `raw_line` を反結合で落とす。
4. `log_rows` / `log_values` へ `INSERT INTO … SELECT`。`row_id` は `MAX + ROW_NUMBER()`。
5. 1 ファイル 1 トランザクション。件数の意味は今と同じ。

## 複数ファイル

- 解析は `min(4, ファイル数, CPU数)` スレッド。`LOGCASE_IMPORT_WORKERS` があれば上限。
- 解析スレッドは案件 DB を開かない。
- 書き込みは選択順。2番が先に解析できても 1 番の書き込み後に入れる。
