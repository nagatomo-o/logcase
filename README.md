# アクセスログ調査ツール

## 起動

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m access_log_db.app
```

ブラウザで表示された `http://127.0.0.1:8765/` を開く。待受はループバックのみ。
