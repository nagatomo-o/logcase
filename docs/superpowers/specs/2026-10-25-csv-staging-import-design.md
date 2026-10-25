# CSV 経由の DuckDB 取り込み

日付: 2026-10-25  
承認: 案 A。`executemany` で一時テーブルへ積むのをやめる。

## 方針

1. Python で今どおり解析する。
2. 通った行と値を UTF-8 CSV に書く。`None` は `\N`。
3. DuckDB の `read_csv` で TEMP テーブルを作る。
4. 先勝ちの反結合と `INSERT … SELECT` は変えない。
5. CSV は書き込み後に消す。追加依存は置かない。
