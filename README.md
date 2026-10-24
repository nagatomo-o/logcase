# アクセスログ調査ツール

## 起動（Docker）

Docker Desktop を起動したうえで:

```powershell
docker compose up --build
```

表示された `http://127.0.0.1:8765/` をブラウザで開く。待受の公開はループバックのみ。案件データは Compose の volume `data` に残る。止めるときは Ctrl+C、または `docker compose down`。

## 起動（仮想環境）

```powershell
.\scripts\run.ps1
```

ポートを変える場合は `.\scripts\run.ps1 --port 9000` のように指定する。
