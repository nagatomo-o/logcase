# Docker 起動

承認済み。Compose が主、`scripts/run.ps1` は残す。

- コンテナ内待受: `0.0.0.0:8765`（環境変数 `ACCESS_LOG_BIND`）
- ホスト公開: `127.0.0.1:8765` のみ
- データ: `./data` を `/app/data` にマウント
- 実行: 現行の Flask 1 プロセス。gunicorn は置かない
- Host ヘッダ検査（`127.0.0.1` / `localhost`）は維持
- 通常の PowerShell 起動の待受は `127.0.0.1` のまま
