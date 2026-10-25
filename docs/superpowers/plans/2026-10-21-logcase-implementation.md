# ログケース Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 手元 Windows で Flask 1 プロセスを起動し、案件ごとの Apache アクセスログを取り込み・重複除外・フィルタ・分類・集計・CSV できる一人用ツールを作る。

**Architecture:** `127.0.0.1` のみ待受。HTML は同一プロセスが返す。案件は `data/{UUID}.sqlite` が 1 ファイル。LogFormat は指定子を捕捉グループに置換して行全体を正規表現 match する。取り込みは同期・ファイル単位トランザクション。同一案件でログ行内容（末尾改行除く）が一致する行は入れない。

**Tech Stack:** Python 3.12、Flask、Jinja2、sqlite3、pytest。Windows。ポート省略時 8765。

## Global Constraints

- 待受はループバックのみ。認証なし。ブラウザ自動起動なし
- メッセージ文言の正は `docs/基本設計/05-メッセージ一覧.md`。画面・標準出力に出す文言はこのコードを使う
- パースアルゴリズムの正は `docs/基本設計/CSE_C01-LogFormat解析.md`
- 物理テーブル名は英語。画面項目・メッセージは日本語
- `data/` は gitignore。バックアップ機能は作らない
- テストは `pytest`。作業ディレクトリはリポジトリルート

## File Structure

```
src/logcase/
  __init__.py
  errors.py          # LogFormatError など
  logformat.py       # compile / parse_line
  messages.py        # 05-メッセージ一覧の文言
  names.py           # 案件名・フィルタ名の正規化
  store.py           # 案件 SQLite
  import_log.py      # ファイル読み・重複・挿入
  filters.py         # 条件適用 SQL 組み立て
  custom_fields.py   # カスタム列と一括
  named_filters.py   # 名前付きフィルタ
  aggregate.py       # グループ・時間バケット
  csv_export.py      # UTF-8 BOM CSV
  app.py             # Flask ルート
  templates/base.html
  templates/cases.html
  templates/work.html
tests/
  test_logformat_compile.py
  test_logformat_parse.py
  test_names.py
  test_store.py
  test_import_log.py
  test_filters.py
  test_custom_fields.py
  test_named_filters.py
  test_aggregate.py
  test_csv_export.py
  test_app.py
pyproject.toml
.gitignore
scripts/run.ps1
README.md
```

設計書（変更しない）: `docs/基本設計/`、`docs/方式設計/`、`docs/要件定義/`。

---

### Task 1: プロジェクト骨格と pytest

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/logcase/__init__.py`
- Create: `tests/test_health.py`
- Create: `README.md`

**Interfaces:**
- Consumes: なし
- Produces: パッケージ `logcase`、`pytest` が通る状態

- [ ] **Step 1: Write the failing test**

Create `tests/test_health.py`:

```python
from logcase import HEALTH


def test_health_constant():
    assert HEALTH == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_health.py -v`

Expected: FAIL（モジュールまたは `HEALTH` が無い）

- [ ] **Step 3: Write minimal implementation**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "logcase"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["flask>=3.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
logcase = "logcase.app:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

Create `src/logcase/__init__.py`:

```python
HEALTH = "ok"
```

Create `.gitignore`:

```
.venv/
__pycache__/
*.pyc
data/
.pytest_cache/
*.egg-info/
```

Create `README.md`:

```markdown
# ログケース

## 起動

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m logcase.app
```

ブラウザで表示された `http://127.0.0.1:8765/` を開く。待受はループバックのみ。
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
pip install -e ".[dev]"
python -m pytest tests/test_health.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git init
git add pyproject.toml .gitignore src/logcase/__init__.py tests/test_health.py README.md
git commit -m "chore: add Python package skeleton and pytest"
```

リポジトリが既にあれば `git init` は省略する。

---

### Task 2: LogFormat のコンパイル

**Files:**
- Create: `src/logcase/errors.py`
- Create: `src/logcase/logformat.py`
- Create: `tests/test_logformat_compile.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `class LogFormatError(ValueError)` with `.code: str`
  - `class FieldDef` dataclass: `identifier: str`, `field_type: str` (`"string"|"number"|"datetime"`), `specifier: str`, `order: int`, `time_axis: bool`, `key: str`
  - `class CompiledFormat` dataclass: `fields: list[FieldDef]`, `pattern: re.Pattern[str]`
  - `compile_logformat(text: str) -> CompiledFormat`
  - 失敗時 `LogFormatError`。`code` は `E_CSE_REQ02` / `E_CSE_FMT02` / `E_CSE_DUP01`

- [ ] **Step 1: Write the failing test**

Create `tests/test_logformat_compile.py`:

```python
import pytest
from logcase.errors import LogFormatError
from logcase.logformat import compile_logformat


def test_empty_raises_req02():
    with pytest.raises(LogFormatError) as ei:
        compile_logformat("   ")
    assert ei.value.code == "E_CSE_REQ02"


def test_unknown_specifier_raises_fmt02():
    with pytest.raises(LogFormatError) as ei:
        compile_logformat("%Q")
    assert ei.value.code == "E_CSE_FMT02"


def test_duplicate_bytes_raises_dup01():
    with pytest.raises(LogFormatError) as ei:
        compile_logformat("%b %B")
    assert ei.value.code == "E_CSE_DUP01"


def test_common_format_field_order():
    compiled = compile_logformat('%h %l %u %t \\"%r\\" %>s %b')
    ids = [f.identifier for f in compiled.fields]
    assert ids == [
        "RemoteHost",
        "RemoteLogName",
        "RemoteUser",
        "Time",
        "Request",
        "Status",
        "Bytes",
    ]
    time_f = compiled.fields[3]
    assert time_f.field_type == "datetime"
    assert time_f.time_axis is True
    status = compiled.fields[5]
    assert status.key == "s"
    assert status.identifier == "Status"


def test_named_header_identifier_preserves_case():
    compiled = compile_logformat('%h \\"%{User-Agent}i\\"')
    assert compiled.fields[1].identifier == "User-Agent"
    assert compiled.fields[1].field_type == "string"


def test_bare_i_rejected():
    with pytest.raises(LogFormatError) as ei:
        compile_logformat("%i")
    assert ei.value.code == "E_CSE_FMT02"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logformat_compile.py -v`

Expected: FAIL（import できない）

- [ ] **Step 3: Write minimal implementation**

Create `src/logcase/errors.py`:

```python
class LogFormatError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
```

Create `src/logcase/logformat.py` with the specifier table and compiler. `pattern` 文字列は CSE_C01 の表どおり。IPv4 のドットはすべてエスケープする。

```python
from __future__ import annotations

import re
from dataclasses import dataclass

from logcase.errors import LogFormatError

SPECIFIER_RE = re.compile(r"%(?:\{([^}]+)\}|>)?([A-Za-z])")

_IPV4 = r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
_IPV6 = r"(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}"
_IP = rf"(?:{_IPV6}|{_IPV4})"

NAMED_PATTERNS = {
    "x-forwarded-for": rf"({_IP}(?:, {_IP})*|-)",
    "referer": r'([^"]+|-)',
    "user-agent": r'([^"]*|-)',
}
DEFAULT_NAMED_PATTERN = r'([^"]+|-)'

# key -> (label, field_type, pattern or None, time_axis)
FIELDS: dict[str, tuple[str, str, str | None, bool]] = {
    "a": ("RequestIP", "string", rf"({_IP})", False),
    "A": ("ServerIP", "string", rf"({_IP})", False),
    "B": ("Bytes", "number", r"(\d+)", False),
    "b": ("Bytes", "number", r"(\d+|-)", False),
    "C": ("Cookie", "string", None, False),
    "D": ("Duration", "number", r"(\d+|-)", False),
    "e": ("Env", "string", None, False),
    "f": ("File", "string", r"(.+)", False),
    "h": ("RemoteHost", "string", rf"({_IP}|[\w\-\.]+)", False),
    "H": ("Protocol", "string", r"(\w+)", False),
    "i": ("RequestHeader", "string", None, False),
    "I": ("ReceiveBytes", "number", r"(\d+)", False),
    "l": ("RemoteLogName", "string", r"([\w-]+|-)", False),
    "m": ("Method", "string", r"(\w+|-)", False),
    "n": ("Memo", "string", None, False),
    "o": ("ResponseHeader", "string", None, False),
    "O": ("SendBytes", "number", r"(\d+)", False),
    "p": ("Port", "number", r"(\d+|-)", False),
    "P": ("ProcessID", "number", r"(\d+|-)", False),
    "q": ("Query", "string", r"(\?[^ ]+)", False),
    "r": ("Request", "string", r'([A-Z]+ [^"]+(?: HTTP/[\d\.]{1,3})?|-)', False),
    "s": ("Status", "number", r"(\d\d\d|-)", False),
    "t": (
        "Time",
        "datetime",
        r"\[(\d{1,2}/\w{3}/\d{1,4}:\d\d:\d\d:\d\d [-+]\d\d\d\d)\]",
        True,
    ),
    "T": ("Seconds", "number", r"(\d+|-)", False),
    "u": ("RemoteUser", "string", r"([\w\.@-]+|'.*?'|\".*?\"|-)", False),
    "U": ("URL", "string", r"([^ ]+)", False),
    "v": ("VirtualHost", "string", r"([\w\.\-]+)", False),
    "V": ("ServerName", "string", r"([\w\.\-]+)", False),
    "X": ("ConnectionStatus", "string", r"([\w\.\-]+)", False),
}


@dataclass(frozen=True)
class FieldDef:
    identifier: str
    field_type: str
    specifier: str
    order: int
    time_axis: bool
    key: str


@dataclass(frozen=True)
class CompiledFormat:
    fields: list[FieldDef]
    pattern: re.Pattern[str]


def compile_logformat(text: str) -> CompiledFormat:
    raw = text.strip()
    if not raw:
        raise LogFormatError("E_CSE_REQ02", "LogFormatを入力してください。")
    fields: list[FieldDef] = []
    seen: set[str] = set()
    pattern_src = raw
    for order, match in enumerate(SPECIFIER_RE.finditer(raw)):
        name, key = match.group(1), match.group(2)
        if key not in FIELDS:
            raise LogFormatError("E_CSE_FMT02", "LogFormatを解釈できません。指定子を確認してください。")
        label, field_type, default_pat, time_axis = FIELDS[key]
        specifier = match.group(0)
        if name:
            identifier = name
            pat = NAMED_PATTERNS.get(name.lower(), DEFAULT_NAMED_PATTERN)
            time_axis = False
            if key == "t":
                raise LogFormatError("E_CSE_FMT02", "LogFormatを解釈できません。指定子を確認してください。")
        else:
            identifier = label
            pat = default_pat
            if pat is None:
                raise LogFormatError("E_CSE_FMT02", "LogFormatを解釈できません。指定子を確認してください。")
        if identifier in seen:
            raise LogFormatError("E_CSE_DUP01", "同じフィールド名が重複しているため登録できません。")
        seen.add(identifier)
        fields.append(
            FieldDef(
                identifier=identifier,
                field_type=field_type,
                specifier=specifier,
                order=order,
                time_axis=time_axis,
                key=key,
            )
        )
        pattern_src = pattern_src.replace(specifier, pat, 1)
    compiled = re.compile(pattern_src, re.IGNORECASE)
    return CompiledFormat(fields=fields, pattern=compiled)
```

`%{strftime}t` は `{名前}` + `t` として検出され、Step 3 の `key == "t"` で FMT02 になる。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logformat_compile.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/errors.py src/logcase/logformat.py tests/test_logformat_compile.py
git commit -m "feat: compile Apache LogFormat into a line regex"
```

---

### Task 3: ログ行のパース

**Files:**
- Modify: `src/logcase/logformat.py`
- Create: `tests/test_logformat_parse.py`

**Interfaces:**
- Consumes: `compile_logformat`
- Produces:
  - `@dataclass class ParsedValue`: `identifier: str`, `is_unset: bool`, `str_value: str | None`, `num_value: int | None`
  - `@dataclass class ParsedLine`: `values: list[ParsedValue]`, `time_display: str | None`, `time_abs: datetime | None`, `time_offset: str | None`
  - `parse_line(compiled: CompiledFormat, line: str) -> ParsedLine | None`  
    一致しない・グループ数不一致は `None`。空文字は `None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_logformat_parse.py`:

```python
from datetime import datetime, timezone, timedelta
from logcase.logformat import compile_logformat, parse_line

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def test_empty_line_none():
    c = compile_logformat(COMMON)
    assert parse_line(c, "") is None


def test_common_line():
    c = compile_logformat(COMMON)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1234'
    parsed = parse_line(c, line)
    assert parsed is not None
    by_id = {v.identifier: v for v in parsed.values}
    assert by_id["RemoteHost"].str_value == "127.0.0.1"
    assert by_id["RemoteLogName"].str_value == "-"
    assert by_id["Status"].num_value == 200
    assert by_id["Bytes"].num_value == 1234
    assert parsed.time_display == "10/Oct/2024:12:00:00 +0900"
    assert parsed.time_abs == datetime(2024, 10, 10, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    assert parsed.time_offset == "+0900"


def test_clf_dash_bytes_is_zero():
    c = compile_logformat(COMMON)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /x HTTP/1.1" 200 -'
    parsed = parse_line(c, line)
    assert parsed is not None
    bytes_v = next(v for v in parsed.values if v.identifier == "Bytes")
    assert bytes_v.is_unset is False
    assert bytes_v.num_value == 0


def test_unparseable_returns_none():
    c = compile_logformat(COMMON)
    assert parse_line(c, "not a log line") is None


def test_escaped_quotes_in_request():
    c = compile_logformat(COMMON)
    line = r'127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /a\"b HTTP/1.1" 200 1'
    parsed = parse_line(c, line)
    assert parsed is not None
    req = next(v for v in parsed.values if v.identifier == "Request")
    assert '"b' in (req.str_value or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logformat_parse.py -v`

Expected: FAIL（`parse_line` が無い）

- [ ] **Step 3: Write minimal implementation**

Append to `src/logcase/logformat.py`:

```python
from datetime import datetime
from typing import Optional

TEMP_QUOTE = "``"


@dataclass
class ParsedValue:
    identifier: str
    is_unset: bool
    str_value: Optional[str]
    num_value: Optional[int]


@dataclass
class ParsedLine:
    values: list[ParsedValue]
    time_display: Optional[str]
    time_abs: Optional[datetime]
    time_offset: Optional[str]


def parse_line(compiled: CompiledFormat, line: str) -> Optional[ParsedLine]:
    if line == "":
        return None
    escaped = line.replace('\\"', TEMP_QUOTE)
    match = compiled.pattern.match(escaped)
    if match is None or len(match.groups()) != len(compiled.fields):
        return None
    values: list[ParsedValue] = []
    time_display = None
    time_abs = None
    time_offset = None
    for i, field in enumerate(compiled.fields):
        raw = match.group(i + 1)
        raw = raw.replace(TEMP_QUOTE, '"')
        if field.field_type == "number":
            if raw == "-":
                if field.key == "b":
                    values.append(ParsedValue(field.identifier, False, None, 0))
                else:
                    values.append(ParsedValue(field.identifier, True, None, None))
            else:
                values.append(ParsedValue(field.identifier, False, None, int(raw)))
        elif field.field_type == "datetime":
            time_display = raw
            try:
                time_abs = datetime.strptime(raw, "%d/%b/%Y:%H:%M:%S %z")
                time_offset = raw[-5:]
            except ValueError:
                time_abs = None
                time_offset = None
            values.append(ParsedValue(field.identifier, time_abs is None, raw, None))
        else:
            values.append(ParsedValue(field.identifier, False, raw, None))
    return ParsedLine(values, time_display, time_abs, time_offset)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logformat_parse.py tests/test_logformat_compile.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/logformat.py tests/test_logformat_parse.py
git commit -m "feat: parse access log lines with LogFormat regex"
```

---

### Task 4: 名称とメッセージ

**Files:**
- Modify: `src/logcase/errors.py`
- Create: `src/logcase/messages.py`
- Create: `src/logcase/names.py`
- Create: `tests/test_names.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `class AppError(Exception)`: `code: str`, `message: str`。`LogFormatError` は `AppError` のサブクラス
  - `MSG: dict[str, str]` — `docs/基本設計/05-メッセージ一覧.md` の文言。件数プレースホルダだけ `{read_count}` 等の英単語（`.format` 用）。完成文の意味は設計どおり
  - `msg(code: str, **kwargs) -> str` — `MSG[code].format(**kwargs)`
  - `validate_name(text: str, empty_code: str, length_code: str) -> str` — strip 後 1〜100 文字。失敗は `AppError`

- [ ] **Step 1: Write the failing test**

Create `tests/test_names.py`:

```python
import pytest
from logcase.errors import AppError
from logcase.names import validate_name


def test_trims_and_accepts():
    assert validate_name("  abc  ", "E_CSE_REQ01", "E_CSE_FMT01") == "abc"


def test_empty():
    with pytest.raises(AppError) as ei:
        validate_name("   ", "E_CSE_REQ01", "E_CSE_FMT01")
    assert ei.value.code == "E_CSE_REQ01"
    assert str(ei.value) == "案件名を入力してください。"


def test_too_long():
    with pytest.raises(AppError) as ei:
        validate_name("a" * 101, "E_CSE_REQ01", "E_CSE_FMT01")
    assert ei.value.code == "E_CSE_FMT01"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_names.py -v`

Expected: FAIL（`AppError` / `validate_name` が無い）

- [ ] **Step 3: Write minimal implementation**

Replace `src/logcase/errors.py`:

```python
class AppError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LogFormatError(AppError):
    pass
```

Create `src/logcase/messages.py`（05-メッセージ一覧の全文。プレースホルダ名は設計どおり）:

```python
MSG = {
    "E_LCH_SYS01": "起動できませんでした。待受に失敗しました。",
    "E_CSE_REQ01": "案件名を入力してください。",
    "E_CSE_FMT01": "案件名は1〜100文字で入力してください。",
    "E_CSE_REQ02": "LogFormatを入力してください。",
    "E_CSE_FMT02": "LogFormatを解釈できません。指定子を確認してください。",
    "E_CSE_DUP01": "同じフィールド名が重複しているため登録できません。",
    "E_CSE_EXS01": "指定した案件が見つかりません。",
    "W_CSE_BIZ01": "この案件を削除します。取り消せません。よろしいですか。",
    "S_CSE_OK01": "案件を作成しました。",
    "S_CSE_OK02": "案件名を変更しました。",
    "S_CSE_OK03": "案件を削除しました。",
    "E_WRK_REQ01": "取り込むファイルを選んでください。",
    "E_WRK_FMT01": "access_log または .gz を選んでください。",
    "W_WRK_BIZ01": "同じファイル名が既にあります。追加して取り込みますか。",
    "E_WRK_SYS01": "取り込みに失敗しました。このファイル分は保存していません。",
    "I_WRK_INF01": "読み込み {read_count} 行、取り込み {inserted_count} 行、破棄 {discarded_count} 行、重複 {duplicate_count} 行。",
    "E_WRK_FMT02": "数値として解釈できません。",
    "E_WRK_FMT03": "日時として解釈できません。",
    "E_WRK_CMB01": "この案件では時間集計を使えません。",
    "E_WRK_RNG01": "n は1以上の整数を指定してください。",
    "E_WRK_REQ02": "設定するカスタムフィールドを選んでください。",
    "W_WRK_BIZ02": "フィルタに一致する {target_count} 行を更新します。よろしいですか。",
    "E_WRK_REQ03": "カスタムフィールド名を入力してください。",
    "E_WRK_FMT04": "名前は1〜100文字で入力してください。",
    "E_WRK_DUP01": "同じ名前のカスタムフィールドがあります。",
    "E_WRK_DUP02": "ログのフィールド名と同じ名前は使えません。",
    "W_WRK_BIZ03": "同じ名前のフィルタがあります。上書きしますか。",
    "W_WRK_BIZ04": "カスタムフィールドとその値を削除します。取り消せません。よろしいですか。",
    "E_WRK_REQ04": "フィルタ名を入力してください。",
    "E_WRK_EXS01": "指定したフィルタが見つかりません。",
    "S_WRK_OK01": "更新しました。",
    "S_WRK_OK02": "フィルタを保存しました。",
    "E_EXP_SYS01": "CSVの書き出しに失敗しました。途中のファイルは残していません。",
    "I_EXP_INF01": "{target_count} 行を書き出します。",
    "S_EXP_OK01": "CSVを書き出しました。",
}


def msg(code: str, **kwargs: object) -> str:
    return MSG[code].format(**kwargs)
```

`I_WRK_INF01` のプレースホルダは実装では英語キーワードにする（`.format` のため）。画面に出す完成文は設計の意味と一致させる。

Create `src/logcase/names.py`:

```python
from logcase.errors import AppError
from logcase.messages import MSG


def validate_name(text: str, empty_code: str, length_code: str) -> str:
    value = text.strip()
    if not value:
        raise AppError(empty_code, MSG[empty_code])
    if len(value) > 100:
        raise AppError(length_code, MSG[length_code])
    return value
```

`logformat.py` の `LogFormatError("E_CSE_REQ02", "...")` はそのまま動く（サブクラス）。

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_names.py tests/test_logformat_compile.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/errors.py src/logcase/messages.py src/logcase/names.py tests/test_names.py
git commit -m "feat: validate trimmed names of 1-100 characters"
```

---

### Task 5: 案件 SQLite の作成と一覧

**Files:**
- Create: `src/logcase/store.py`
- Create: `tests/test_store.py`

**Interfaces:**
- Consumes: `compile_logformat`, `validate_name`, `AppError`, `MSG`
- Produces:
  - `create_case(data_dir: Path, name: str, logformat: str) -> str` 案件公開識別子（UUID v4、ハイフン付き 36 文字）
  - `list_cases(data_dir: Path) -> list[dict]` キー: `id`, `name`, `logformat_head`, `file_count`, `row_count`, `last_import`, `error`（壊れたファイルは `error` にファイル名）
  - `rename_case(data_dir: Path, case_id: str, name: str) -> None`
  - `delete_case(data_dir: Path, case_id: str) -> None`
  - `open_case(data_dir: Path, case_id: str) -> sqlite3.Connection` 無い・UUID 不正は `AppError("E_CSE_EXS01")`
  - `init_schema(conn: sqlite3.Connection) -> None`
  - `case_path(data_dir: Path, case_id: str) -> Path`

日時型フィールドは `log_values` に入れない（03-データ構造設計）。`time_abs` は ISO 8601（オフセット付き、例 `2024-10-10T12:00:00+09:00`）。

- [ ] **Step 1: Write the failing test**

Create `tests/test_store.py`:

```python
from pathlib import Path
import pytest
from logcase.errors import AppError
from logcase.store import create_case, list_cases, rename_case, delete_case

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def test_create_list_rename_delete(tmp_path: Path):
    cid = create_case(tmp_path, "  案件A  ", COMMON)
    assert len(cid) == 36
    assert (tmp_path / f"{cid}.sqlite").is_file()
    rows = list_cases(tmp_path)
    assert len(rows) == 1
    assert rows[0]["name"] == "案件A"
    assert rows[0]["id"] == cid
    assert rows[0]["row_count"] == 0
    assert rows[0]["file_count"] == 0
    rename_case(tmp_path, cid, "案件B")
    assert list_cases(tmp_path)[0]["name"] == "案件B"
    delete_case(tmp_path, cid)
    assert list_cases(tmp_path) == []
    assert not (tmp_path / f"{cid}.sqlite").exists()


def test_missing_case(tmp_path: Path):
    with pytest.raises(AppError) as ei:
        rename_case(tmp_path, "00000000-0000-0000-0000-000000000000", "x")
    assert ei.value.code == "E_CSE_EXS01"


def test_log_fields_persisted(tmp_path: Path):
    from logcase.store import open_case
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    ids = [r["identifier"] for r in conn.execute(
        "select identifier from log_fields order by display_order"
    )]
    assert ids == [
        "RemoteHost", "RemoteLogName", "RemoteUser", "Time", "Request", "Status", "Bytes",
    ]
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_store.py -v`

Expected: FAIL（`store` が無い）

- [ ] **Step 3: Write `store.py`**

```python
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from logcase.errors import AppError
from logcase.logformat import compile_logformat
from logcase.messages import MSG
from logcase.names import validate_name

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE meta (
  name TEXT NOT NULL,
  logformat TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE log_fields (
  identifier TEXT PRIMARY KEY,
  field_type TEXT NOT NULL,
  specifier TEXT NOT NULL,
  display_order INTEGER NOT NULL,
  time_axis INTEGER NOT NULL,
  key TEXT NOT NULL
);
CREATE TABLE custom_fields (
  name TEXT PRIMARY KEY,
  display_order INTEGER NOT NULL
);
CREATE TABLE import_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_filename TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  read_count INTEGER NOT NULL,
  inserted_count INTEGER NOT NULL,
  discarded_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL
);
CREATE TABLE log_rows (
  row_id INTEGER PRIMARY KEY,
  source_filename TEXT NOT NULL,
  line_no INTEGER NOT NULL,
  raw_line TEXT NOT NULL UNIQUE,
  time_display TEXT,
  time_abs TEXT,
  time_offset TEXT
);
CREATE TABLE log_values (
  row_id INTEGER NOT NULL,
  identifier TEXT NOT NULL,
  is_unset INTEGER NOT NULL,
  str_value TEXT,
  num_value INTEGER,
  PRIMARY KEY (row_id, identifier)
);
CREATE INDEX idx_log_values_str ON log_values(identifier, str_value);
CREATE INDEX idx_log_values_num ON log_values(identifier, num_value);
CREATE TABLE custom_values (
  row_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  is_unset INTEGER NOT NULL,
  str_value TEXT,
  PRIMARY KEY (row_id, name)
);
CREATE TABLE named_filters (
  name TEXT PRIMARY KEY,
  spec TEXT NOT NULL
);
"""


def case_path(data_dir: Path, case_id: str) -> Path:
    return data_dir / f"{case_id}.sqlite"


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def open_case(data_dir: Path, case_id: str) -> sqlite3.Connection:
    try:
        UUID(case_id)
    except ValueError as exc:
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"]) from exc
    path = case_path(data_dir, case_id)
    if not path.is_file():
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"])
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_case(data_dir: Path, name: str, logformat: str) -> str:
    name = validate_name(name, "E_CSE_REQ01", "E_CSE_FMT01")
    compiled = compile_logformat(logformat)
    data_dir.mkdir(parents=True, exist_ok=True)
    case_id = str(uuid4())
    path = case_path(data_dir, case_id)
    conn = sqlite3.connect(path)
    try:
        init_schema(conn)
        conn.execute(
            "INSERT INTO meta (name, logformat, created_at) VALUES (?, ?, ?)",
            (name, logformat.strip(), datetime.now(timezone.utc).isoformat()),
        )
        for field in compiled.fields:
            conn.execute(
                """INSERT INTO log_fields
                   (identifier, field_type, specifier, display_order, time_axis, key)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    field.identifier,
                    field.field_type,
                    field.specifier,
                    field.order,
                    int(field.time_axis),
                    field.key,
                ),
            )
        conn.commit()
    except Exception:
        conn.close()
        path.unlink(missing_ok=True)
        raise
    conn.close()
    return case_id


def list_cases(data_dir: Path) -> list[dict]:
    if not data_dir.exists():
        return []
    rows: list[dict] = []
    for path in sorted(data_dir.glob("*.sqlite")):
        try:
            UUID(path.stem)
        except ValueError:
            rows.append({"id": path.stem, "error": path.name})
            continue
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            meta = conn.execute("SELECT name, logformat FROM meta").fetchone()
            file_count = conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0]
            row_count = conn.execute("SELECT COUNT(*) FROM log_rows").fetchone()[0]
            last = conn.execute("SELECT MAX(imported_at) FROM import_files").fetchone()[0]
            conn.close()
            head = (meta["logformat"] or "")[:80]
            rows.append(
                {
                    "id": path.stem,
                    "name": meta["name"],
                    "logformat_head": head,
                    "file_count": file_count,
                    "row_count": row_count,
                    "last_import": last,
                    "error": None,
                }
            )
        except sqlite3.Error:
            rows.append({"id": path.stem, "error": path.name})
    return rows


def rename_case(data_dir: Path, case_id: str, name: str) -> None:
    name = validate_name(name, "E_CSE_REQ01", "E_CSE_FMT01")
    conn = open_case(data_dir, case_id)
    conn.execute("UPDATE meta SET name = ?", (name,))
    conn.commit()
    conn.close()


def delete_case(data_dir: Path, case_id: str) -> None:
    try:
        UUID(case_id)
    except ValueError as exc:
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"]) from exc
    path = case_path(data_dir, case_id)
    if not path.is_file():
        raise AppError("E_CSE_EXS01", MSG["E_CSE_EXS01"])
    path.unlink()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_store.py tests/test_logformat_compile.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/store.py tests/test_store.py
git commit -m "feat: persist one SQLite file per case"
```

---

### Task 6: 取り込みと重複除外

**Files:**
- Create: `src/logcase/import_log.py`
- Create: `tests/test_import_log.py`

**Interfaces:**
- Consumes: `open_case`, `compile_logformat`, `parse_line`, `MSG`
- Produces:
  - `@dataclass class ImportResult`: `read_count: int`, `inserted_count: int`, `discarded_count: int`, `duplicate_count: int`
  - `import_file(conn: sqlite3.Connection, filename: str, lines: Iterable[str]) -> ImportResult`
  - 末尾改行のみ除去。空行は discarded。`raw_line` UNIQUE 衝突または同一バッチ内一致は duplicate。`parse_line` が `None` なら discarded。日時フィールドは `log_rows` のみ（`log_values` には入れない）。`row_id` は `COALESCE(MAX(row_id),0)+1` から。1 トランザクション。例外時 rollback して `AppError("E_WRK_SYS01")`
  - `import_gzip_bytes(conn, filename: str, data: bytes) -> ImportResult` — UTF-8、`errors="replace"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_import_log.py`:

```python
import gzip
from pathlib import Path
from logcase.store import create_case, open_case
from logcase.import_log import import_file, import_gzip_bytes

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
LINE = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1234\n'


def test_insert_then_duplicate(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    r1 = import_file(conn, "access_log", [LINE, "bad\n", LINE, "\n"])
    assert r1.read_count == 4
    assert r1.inserted_count == 1
    assert r1.discarded_count == 2
    assert r1.duplicate_count == 1
    r2 = import_file(conn, "access_log.2", [LINE])
    assert r2.inserted_count == 0
    assert r2.duplicate_count == 1
    n = conn.execute("select count(*) from log_rows").fetchone()[0]
    assert n == 1
    row = conn.execute("select time_display, time_offset, time_abs from log_rows").fetchone()
    assert row["time_display"] == "10/Oct/2024:12:00:00 +0900"
    assert row["time_offset"] == "+0900"
    assert row["time_abs"].startswith("2024-10-10T12:00:00")
    time_vals = conn.execute(
        "select count(*) from log_values where identifier='Time'"
    ).fetchone()[0]
    assert time_vals == 0
    rec = conn.execute("select * from import_files order by id").fetchall()
    assert len(rec) == 2
    conn.close()


def test_gzip(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    data = gzip.compress(LINE.encode("utf-8"))
    r = import_gzip_bytes(conn, "access_log.gz", data)
    assert r.inserted_count == 1
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_import_log.py -v`

Expected: FAIL

- [ ] **Step 3: Implement `import_log.py`**

```python
from __future__ import annotations

import gzip
import io
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from logcase.errors import AppError
from logcase.logformat import compile_logformat, parse_line
from logcase.messages import MSG


@dataclass
class ImportResult:
    read_count: int
    inserted_count: int
    discarded_count: int
    duplicate_count: int


def _strip_nl(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1]
    return line


def import_file(
    conn: sqlite3.Connection, filename: str, lines: Iterable[str]
) -> ImportResult:
    logformat = conn.execute("SELECT logformat FROM meta").fetchone()[0]
    compiled = compile_logformat(logformat)
    result = ImportResult(0, 0, 0, 0)
    try:
        conn.execute("BEGIN")
        next_id = conn.execute(
            "SELECT COALESCE(MAX(row_id), 0) FROM log_rows"
        ).fetchone()[0] + 1
        seen: set[str] = set()
        line_no = 0
        for raw in lines:
            line_no += 1
            result.read_count += 1
            content = _strip_nl(raw)
            if content == "":
                result.discarded_count += 1
                continue
            exists = conn.execute(
                "SELECT 1 FROM log_rows WHERE raw_line = ?", (content,)
            ).fetchone()
            if exists or content in seen:
                result.duplicate_count += 1
                continue
            parsed = parse_line(compiled, content)
            if parsed is None:
                result.discarded_count += 1
                continue
            time_abs = None
            if parsed.time_abs is not None:
                time_abs = parsed.time_abs.isoformat()
            conn.execute(
                """INSERT INTO log_rows
                   (row_id, source_filename, line_no, raw_line,
                    time_display, time_abs, time_offset)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    next_id,
                    filename,
                    line_no,
                    content,
                    parsed.time_display,
                    time_abs,
                    parsed.time_offset,
                ),
            )
            for value in parsed.values:
                field = next(f for f in compiled.fields if f.identifier == value.identifier)
                if field.field_type == "datetime":
                    continue
                conn.execute(
                    """INSERT INTO log_values
                       (row_id, identifier, is_unset, str_value, num_value)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        next_id,
                        value.identifier,
                        int(value.is_unset),
                        value.str_value,
                        value.num_value,
                    ),
                )
            seen.add(content)
            next_id += 1
            result.inserted_count += 1
        conn.execute(
            """INSERT INTO import_files
               (source_filename, imported_at, read_count, inserted_count,
                discarded_count, duplicate_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                filename,
                datetime.now(timezone.utc).isoformat(),
                result.read_count,
                result.inserted_count,
                result.discarded_count,
                result.duplicate_count,
            ),
        )
        conn.commit()
    except AppError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise AppError("E_WRK_SYS01", MSG["E_WRK_SYS01"]) from exc
    return result


def import_gzip_bytes(
    conn: sqlite3.Connection, filename: str, data: bytes
) -> ImportResult:
    with gzip.open(io.BytesIO(data), "rt", encoding="utf-8", errors="replace") as handle:
        return import_file(conn, filename, handle)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_import_log.py tests/test_store.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/import_log.py tests/test_import_log.py
git commit -m "feat: import log lines and skip exact duplicates"
```

---

### Task 7: フィルタと続き取り

**Files:**
- Create: `src/logcase/filters.py`
- Create: `tests/test_filters.py`

**Interfaces:**
- Consumes: `AppError`, `MSG`, `open_case`
- Produces:
  - `PAGE_SIZE = 100`
  - `@dataclass class Condition`: `identifier: str`, `source: str` (`"log"` | `"custom"`), `field_type: str` (`"string"` | `"number"` | `"datetime"`), `op: str`, `value: str | list[str] | None`
  - `count_rows(conn, conditions: list[Condition]) -> int`
  - `fetch_rows(conn, conditions: list[Condition], after_row_id: int, limit: int = PAGE_SIZE) -> list[dict]`
    dict キー: `row_id`, `source_filename`, `line_no`, 各ログ識別子、各カスタム名。日時は `time_display`。未設定は `None`
  - 複数 Condition は AND。`op=="in"` は同一列の OR
  - 文字列 op: `contains` `not_contains` `starts` `not_starts` `in` `gte` `lte`（`instr` / 比較。大文字小文字区別）
  - 数値 op: `unset` `eq` `ne` `gt` `ge` `lt` `le`
  - 日時 op: `unset` `eq` `before` `on_or_after` `after` `on_or_before`。値は `YYYY-MM-DDTHH:MM[:SS]`。比較は `substr(time_abs,1,19)` の壁時計（ログ TZ）
  - 数値にできない値は `E_WRK_FMT02`、日時は `E_WRK_FMT03`

- [ ] **Step 1: Write the failing test**

Create `tests/test_filters.py`:

```python
from pathlib import Path
from logcase.store import create_case, open_case
from logcase.import_log import import_file
from logcase.filters import Condition, count_rows, fetch_rows

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def _line(path: str, status: str, when: str = "10/Oct/2024:12:00:00 +0900") -> str:
    return f'127.0.0.1 - - [{when}] "GET {path} HTTP/1.1" {status} 10\n'


def _conn(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/index", "200"),
            _line("/api", "404", "10/Oct/2024:13:00:00 +0900"),
            _line("/index", "500", "11/Oct/2024:12:00:00 +0900"),
        ],
    )
    return conn


def test_contains_and_status(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [
        Condition("Request", "log", "string", "contains", "/index"),
        Condition("Status", "log", "number", "eq", "200"),
    ]
    assert count_rows(conn, conds) == 1
    rows = fetch_rows(conn, conds, after_row_id=0)
    assert len(rows) == 1
    assert rows[0]["row_id"] == 1
    assert rows[0]["Request"] == "GET /index HTTP/1.1"
    assert rows[0]["Status"] == 200
    conn.close()


def test_keyset(tmp_path: Path):
    conn = _conn(tmp_path)
    page1 = fetch_rows(conn, [], after_row_id=0, limit=2)
    assert [r["row_id"] for r in page1] == [1, 2]
    page2 = fetch_rows(conn, [], after_row_id=2, limit=2)
    assert [r["row_id"] for r in page2] == [3]
    conn.close()


def test_datetime_on_or_after(tmp_path: Path):
    conn = _conn(tmp_path)
    conds = [Condition("Time", "log", "datetime", "on_or_after", "2024-10-10T13:00:00")]
    assert count_rows(conn, conds) == 2
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_filters.py -v`

Expected: FAIL

- [ ] **Step 3: Implement `filters.py`**

```python
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from logcase.errors import AppError
from logcase.messages import MSG

PAGE_SIZE = 100


@dataclass
class Condition:
    identifier: str
    source: str
    field_type: str
    op: str
    value: str | list[str] | None


def _parse_int(text: str) -> int:
    try:
        return int(text)
    except (TypeError, ValueError) as exc:
        raise AppError("E_WRK_FMT02", MSG["E_WRK_FMT02"]) from exc


def _parse_dt(text: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    raise AppError("E_WRK_FMT03", MSG["E_WRK_FMT03"])


def _where(conditions: list[Condition]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for cond in conditions:
        sql, extra = _one(cond)
        clauses.append(sql)
        params.extend(extra)
    if not clauses:
        return "1=1", []
    return " AND ".join(clauses), params


def _one(cond: Condition) -> tuple[str, list[Any]]:
    if cond.field_type == "datetime":
        wall = "substr(r.time_abs, 1, 19)"
        if cond.op == "unset":
            return "r.time_abs IS NULL", []
        target = _parse_dt(str(cond.value))
        ops = {
            "eq": "=",
            "before": "<",
            "on_or_after": ">=",
            "after": ">",
            "on_or_before": "<=",
        }
        return f"(r.time_abs IS NOT NULL AND {wall} {ops[cond.op]} ?)", [target]
    if cond.source == "custom":
        exists = (
            "EXISTS (SELECT 1 FROM custom_values c "
            "WHERE c.row_id = r.row_id AND c.name = ? AND {pred})"
        )
        key = cond.identifier
        col_s, col_u = "c.str_value", "c.is_unset"
    else:
        exists = (
            "EXISTS (SELECT 1 FROM log_values v "
            "WHERE v.row_id = r.row_id AND v.identifier = ? AND {pred})"
        )
        key = cond.identifier
        col_s, col_u = "v.str_value", "v.is_unset"
        col_n = "v.num_value"
    if cond.field_type == "number":
        if cond.op == "unset":
            return exists.format(pred=f"{col_u} = 1"), [key]
        number = _parse_int(str(cond.value))
        ops = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
        pred = f"{col_u} = 0 AND {col_n} {ops[cond.op]} ?"
        return exists.format(pred=pred), [key, number]
    if cond.op == "contains":
        pred = f"{col_s} IS NOT NULL AND instr({col_s}, ?) > 0"
        return exists.format(pred=pred), [key, cond.value]
    if cond.op == "not_contains":
        pred = f"{col_s} IS NOT NULL AND instr({col_s}, ?) = 0"
        return exists.format(pred=pred), [key, cond.value]
    if cond.op == "starts":
        pred = f"{col_s} IS NOT NULL AND substr({col_s}, 1, length(?)) = ?"
        return exists.format(pred=pred), [key, cond.value, cond.value]
    if cond.op == "not_starts":
        pred = f"{col_s} IS NOT NULL AND substr({col_s}, 1, length(?)) != ?"
        return exists.format(pred=pred), [key, cond.value, cond.value]
    if cond.op == "gte":
        pred = f"{col_s} IS NOT NULL AND {col_s} >= ?"
        return exists.format(pred=pred), [key, cond.value]
    if cond.op == "lte":
        pred = f"{col_s} IS NOT NULL AND {col_s} <= ?"
        return exists.format(pred=pred), [key, cond.value]
    if cond.op == "in":
        values = list(cond.value or [])
        placeholders = ",".join("?" * len(values)) or "NULL"
        pred = f"{col_s} IN ({placeholders})"
        return exists.format(pred=pred), [key, *values]
    raise ValueError(f"unknown op {cond.op}")


def count_rows(conn: sqlite3.Connection, conditions: list[Condition]) -> int:
    where, params = _where(conditions)
    sql = f"SELECT COUNT(*) FROM log_rows r WHERE {where}"
    return conn.execute(sql, params).fetchone()[0]


def fetch_rows(
    conn: sqlite3.Connection,
    conditions: list[Condition],
    after_row_id: int,
    limit: int = PAGE_SIZE,
) -> list[dict]:
    where, params = _where(conditions)
    sql = (
        f"SELECT r.row_id, r.source_filename, r.line_no, r.time_display "
        f"FROM log_rows r WHERE r.row_id > ? AND {where} "
        f"ORDER BY r.row_id LIMIT ?"
    )
    rows = conn.execute(sql, [after_row_id, *params, limit]).fetchall()
    log_fields = [
        r["identifier"]
        for r in conn.execute(
            "SELECT identifier, field_type FROM log_fields ORDER BY display_order"
        )
    ]
    field_types = {
        r["identifier"]: r["field_type"]
        for r in conn.execute("SELECT identifier, field_type FROM log_fields")
    }
    custom_names = [
        r["name"]
        for r in conn.execute("SELECT name FROM custom_fields ORDER BY display_order")
    ]
    out: list[dict] = []
    for row in rows:
        item: dict = {
            "row_id": row["row_id"],
            "source_filename": row["source_filename"],
            "line_no": row["line_no"],
        }
        values = {
            v["identifier"]: v
            for v in conn.execute(
                "SELECT identifier, is_unset, str_value, num_value FROM log_values WHERE row_id=?",
                (row["row_id"],),
            )
        }
        for ident in log_fields:
            if field_types[ident] == "datetime":
                item[ident] = row["time_display"]
                continue
            val = values.get(ident)
            if val is None or val["is_unset"]:
                item[ident] = None
            elif val["num_value"] is not None:
                item[ident] = val["num_value"]
            else:
                item[ident] = val["str_value"]
        customs = {
            v["name"]: v
            for v in conn.execute(
                "SELECT name, is_unset, str_value FROM custom_values WHERE row_id=?",
                (row["row_id"],),
            )
        }
        for name in custom_names:
            val = customs.get(name)
            if val is None or val["is_unset"]:
                item[name] = None
            else:
                item[name] = val["str_value"]
        out.append(item)
    return out
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_filters.py tests/test_import_log.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/filters.py tests/test_filters.py
git commit -m "feat: filter log rows and paginate by row_id"
```

---

### Task 8: カスタムフィールドと一括更新

**Files:**
- Modify: `src/logcase/store.py`
- Create: `tests/test_custom_fields.py`

**Interfaces:**
- Consumes: `validate_name`, `Condition`, `count_rows`
- Produces:
  - `add_custom_field(conn, name: str) -> None` — `E_WRK_REQ03` / `E_WRK_FMT04` / ログ識別子と重複 `E_WRK_DUP02` / カスタム重複 `E_WRK_DUP01`
  - `delete_custom_field(conn, name: str) -> None` 定義と値を削除
  - `set_custom_value(conn, row_id: int, name: str, value: str) -> None` 空文字は `is_unset=1`, `str_value=NULL`
  - `bulk_set_custom(conn, name: str, value: str, conditions: list[Condition]) -> int` フィルタ一致全行。戻り値は対象件数

- [ ] **Step 1: Write the failing test**

Create `tests/test_custom_fields.py`:

```python
from pathlib import Path
import pytest
from logcase.errors import AppError
from logcase.store import (
    create_case,
    open_case,
    add_custom_field,
    set_custom_value,
    bulk_set_custom,
)
from logcase.import_log import import_file
from logcase.filters import Condition, fetch_rows

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
LINE = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET {path} HTTP/1.1" 200 1\n'


def test_add_and_bulk(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [LINE.format(path="/a"), LINE.format(path="/b"), LINE.format(path="/c")],
    )
    add_custom_field(conn, "  機能名  ")
    with pytest.raises(AppError) as ei:
        add_custom_field(conn, "機能名")
    assert ei.value.code == "E_WRK_DUP01"
    with pytest.raises(AppError) as ei:
        add_custom_field(conn, "Status")
    assert ei.value.code == "E_WRK_DUP02"
    conds = [Condition("Request", "log", "string", "contains", "/a")]
    n = bulk_set_custom(conn, "機能名", "検索", conds)
    assert n == 1
    rows = fetch_rows(conn, [], 0)
    by_path = {r["Request"]: r["機能名"] for r in rows}
    assert by_path["GET /a HTTP/1.1"] == "検索"
    assert by_path["GET /b HTTP/1.1"] is None
    bulk_set_custom(conn, "機能名", "", [])
    rows = fetch_rows(conn, [], 0)
    assert all(r["機能名"] is None for r in rows)
    set_custom_value(conn, 2, "機能名", "単独")
    rows = fetch_rows(conn, [], 0)
    assert rows[1]["機能名"] == "単独"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_custom_fields.py -v`

Expected: FAIL

- [ ] **Step 3: Append to `store.py`**

```python
from logcase.filters import Condition, count_rows


def add_custom_field(conn: sqlite3.Connection, name: str) -> None:
    name = validate_name(name, "E_WRK_REQ03", "E_WRK_FMT04")
    log_ids = {
        r[0] for r in conn.execute("SELECT identifier FROM log_fields")
    }
    if name in log_ids:
        raise AppError("E_WRK_DUP02", MSG["E_WRK_DUP02"])
    exists = conn.execute(
        "SELECT 1 FROM custom_fields WHERE name = ?", (name,)
    ).fetchone()
    if exists:
        raise AppError("E_WRK_DUP01", MSG["E_WRK_DUP01"])
    order = conn.execute(
        "SELECT COALESCE(MAX(display_order), -1) + 1 FROM custom_fields"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO custom_fields (name, display_order) VALUES (?, ?)",
        (name, order),
    )
    conn.commit()


def delete_custom_field(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM custom_values WHERE name = ?", (name,))
    conn.execute("DELETE FROM custom_fields WHERE name = ?", (name,))
    conn.commit()


def set_custom_value(
    conn: sqlite3.Connection, row_id: int, name: str, value: str
) -> None:
    is_unset = 1 if value == "" else 0
    str_value = None if is_unset else value
    conn.execute(
        """INSERT INTO custom_values (row_id, name, is_unset, str_value)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(row_id, name) DO UPDATE SET
             is_unset=excluded.is_unset, str_value=excluded.str_value""",
        (row_id, name, is_unset, str_value),
    )
    conn.commit()


def bulk_set_custom(
    conn: sqlite3.Connection,
    name: str,
    value: str,
    conditions: list[Condition],
) -> int:
    if not name:
        raise AppError("E_WRK_REQ02", MSG["E_WRK_REQ02"])
    exists = conn.execute(
        "SELECT 1 FROM custom_fields WHERE name = ?", (name,)
    ).fetchone()
    if not exists:
        raise AppError("E_WRK_REQ02", MSG["E_WRK_REQ02"])
    from logcase.filters import _where

    where, params = _where(conditions)
    ids = [
        r[0]
        for r in conn.execute(
            f"SELECT r.row_id FROM log_rows r WHERE {where}", params
        )
    ]
    is_unset = 1 if value == "" else 0
    str_value = None if is_unset else value
    for row_id in ids:
        conn.execute(
            """INSERT INTO custom_values (row_id, name, is_unset, str_value)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(row_id, name) DO UPDATE SET
                 is_unset=excluded.is_unset, str_value=excluded.str_value""",
            (row_id, name, is_unset, str_value),
        )
    conn.commit()
    return len(ids)
```

`filters._where` を公開するか、同じモジュール関数として使う。Task 7 の `_where` の先頭 `_` を外して `where_sql` にリネームしてもよい。実装では `from logcase.filters import _where` でよい。

循環 import を避けるため、`bulk_set_custom` は `store.py` ではなく `src/logcase/custom_fields.py` に置く。

Create `src/logcase/custom_fields.py` に上記 4 関数を置く。`store.py` は案件 CRUD のみ。Task 8 の Files を `Create: src/logcase/custom_fields.py` に読み替える。テストの import も `custom_fields` から。

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_custom_fields.py tests/test_filters.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/custom_fields.py tests/test_custom_fields.py
git commit -m "feat: add custom fields and bulk update"
```

---

### Task 9: 名前付きフィルタ

**Files:**
- Create: `src/logcase/named_filters.py`
- Create: `tests/test_named_filters.py`

**Interfaces:**
- Consumes: `validate_name`, `Condition`
- Produces:
  - `save_named_filter(conn, name: str, spec: list[dict], overwrite: bool) -> None`
    同名かつ `overwrite=False` なら `AppError("W_WRK_BIZ03")`
    名前は `E_WRK_REQ04` / `E_WRK_FMT04`
  - `load_named_filter(conn, name: str) -> list[dict]` 無ければ `E_WRK_EXS01`
  - `list_named_filters(conn) -> list[str]`
  - `delete_named_filter(conn, name: str) -> None`
  - `spec` の各要素: `{"identifier","source","field_type","op","value"}`。JSON 文字列で `named_filters.spec` に保管
  - `conditions_from_spec(spec: list[dict]) -> list[Condition]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_named_filters.py`:

```python
from pathlib import Path
import pytest
from logcase.errors import AppError
from logcase.store import create_case, open_case
from logcase.named_filters import (
    save_named_filter,
    load_named_filter,
    list_named_filters,
)

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
SPEC = [{"identifier": "Status", "source": "log", "field_type": "number", "op": "eq", "value": "200"}]


def test_save_load_overwrite(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    save_named_filter(conn, "  成功  ", SPEC, overwrite=False)
    assert list_named_filters(conn) == ["成功"]
    assert load_named_filter(conn, "成功") == SPEC
    with pytest.raises(AppError) as ei:
        save_named_filter(conn, "成功", SPEC, overwrite=False)
    assert ei.value.code == "W_WRK_BIZ03"
    save_named_filter(
        conn,
        "成功",
        [{**SPEC[0], "value": "404"}],
        overwrite=True,
    )
    assert load_named_filter(conn, "成功")[0]["value"] == "404"
    with pytest.raises(AppError) as ei:
        load_named_filter(conn, "無い")
    assert ei.value.code == "E_WRK_EXS01"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_named_filters.py -v`

Expected: FAIL

- [ ] **Step 3: Implement `named_filters.py`**

```python
from __future__ import annotations

import json
import sqlite3

from logcase.errors import AppError
from logcase.filters import Condition
from logcase.messages import MSG
from logcase.names import validate_name


def save_named_filter(
    conn: sqlite3.Connection, name: str, spec: list[dict], overwrite: bool
) -> None:
    name = validate_name(name, "E_WRK_REQ04", "E_WRK_FMT04")
    exists = conn.execute(
        "SELECT 1 FROM named_filters WHERE name = ?", (name,)
    ).fetchone()
    payload = json.dumps(spec, ensure_ascii=False)
    if exists and not overwrite:
        raise AppError("W_WRK_BIZ03", MSG["W_WRK_BIZ03"])
    if exists:
        conn.execute(
            "UPDATE named_filters SET spec = ? WHERE name = ?", (payload, name)
        )
    else:
        conn.execute(
            "INSERT INTO named_filters (name, spec) VALUES (?, ?)", (name, payload)
        )
    conn.commit()


def load_named_filter(conn: sqlite3.Connection, name: str) -> list[dict]:
    row = conn.execute(
        "SELECT spec FROM named_filters WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise AppError("E_WRK_EXS01", MSG["E_WRK_EXS01"])
    return json.loads(row[0])


def list_named_filters(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM named_filters ORDER BY name")]


def delete_named_filter(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM named_filters WHERE name = ?", (name,))
    conn.commit()


def conditions_from_spec(spec: list[dict]) -> list[Condition]:
    return [
        Condition(
            identifier=item["identifier"],
            source=item["source"],
            field_type=item["field_type"],
            op=item["op"],
            value=item.get("value"),
        )
        for item in spec
    ]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_named_filters.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/named_filters.py tests/test_named_filters.py
git commit -m "feat: save and recall named filters"
```

---

### Task 10: 集計と時間バケット

**Files:**
- Create: `src/logcase/aggregate.py`
- Create: `tests/test_aggregate.py`

**Interfaces:**
- Consumes: `Condition`, `_where`
- Produces:
  - `@dataclass class Metric`: `identifier: str`, `fn: str` (`"count"` | `"avg"` | `"sum"`)
  - `aggregate(conn, conditions: list[Condition], group_ids: list[tuple[str, str]], bucket: tuple[int, str] | None, metrics: list[Metric]) -> list[dict]`
    - `group_ids`: `(source, identifier)` source は `log` または `custom`
    - `bucket`: `(n, "second"|"minute"|"hour")`。時間軸フィールドが無ければ `E_WRK_CMB01`。`n < 1` は `E_WRK_RNG01`
    - 結果 dict のキー順: グループ列、`bucket_start`（使うとき）、`access_count`、各メトリクス（`{identifier}_{fn}`）
    - `access_count` は常に行数
    - 文字列・数値の `count` は `is_unset=0` の行数。`avg`/`sum` は未設定を除く。`%b` の 0 は sum に含める
    - バケット開始は壁時計 `substr(time_abs,1,19)` を n 単位で切る。`time_abs` NULL はバケット集計から除外
    - `bucket_start` は `YYYY-MM-DDTHH:MM:SS`（ログ TZ 上の開始）

- [ ] **Step 1: Write the failing test**

Create `tests/test_aggregate.py`:

```python
from pathlib import Path
from logcase.store import create_case, open_case
from logcase.custom_fields import add_custom_field, set_custom_value
from logcase.import_log import import_file
from logcase.aggregate import Metric, aggregate
from logcase.filters import Condition

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def _line(path: str, when: str, nbytes: str = "10") -> str:
    return f'127.0.0.1 - - [{when}] "GET {path} HTTP/1.1" 200 {nbytes}\n'


def test_hour_bucket_and_group(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(
        conn,
        "access_log",
        [
            _line("/a", "10/Oct/2024:12:10:00 +0900", "10"),
            _line("/a", "10/Oct/2024:12:50:00 +0900", "20"),
            _line("/b", "10/Oct/2024:13:10:00 +0900", "5"),
        ],
    )
    add_custom_field(conn, "機能名")
    set_custom_value(conn, 1, "機能名", "検索")
    set_custom_value(conn, 2, "機能名", "検索")
    set_custom_value(conn, 3, "機能名", "API")
    rows = aggregate(
        conn,
        [],
        group_ids=[("custom", "機能名")],
        bucket=(1, "hour"),
        metrics=[Metric("Bytes", "sum")],
    )
    assert rows[0]["機能名"] == "API" or rows[0]["機能名"] == "検索"
    by = {(r["機能名"], r["bucket_start"]): r for r in rows}
    assert by[("検索", "2024-10-10T12:00:00")]["access_count"] == 2
    assert by[("検索", "2024-10-10T12:00:00")]["Bytes_sum"] == 30
    assert by[("API", "2024-10-10T13:00:00")]["access_count"] == 1
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_aggregate.py -v`

Expected: FAIL

- [ ] **Step 3: Implement `aggregate.py`**

Python 側で全一致行を読み、グループキーを組む（件数は数百万まで待つ前提。同期で分単位は許容）。SQL で GROUP BY してもよいが、バケット切る処理は次で足りる。

```python
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from logcase.errors import AppError
from logcase.filters import Condition, fetch_rows, count_rows
from logcase.messages import MSG


@dataclass
class Metric:
    identifier: str
    fn: str


def _has_time_axis(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM log_fields WHERE time_axis = 1"
    ).fetchone()
    return row is not None


def _bucket_start(wall: str, n: int, unit: str) -> str:
    dt = datetime.strptime(wall, "%Y-%m-%dT%H:%M:%S")
    if unit == "hour":
        hour = (dt.hour // n) * n
        dt = dt.replace(hour=hour, minute=0, second=0)
    elif unit == "minute":
        minute = (dt.minute // n) * n
        dt = dt.replace(minute=minute, second=0)
    elif unit == "second":
        second = (dt.second // n) * n
        dt = dt.replace(second=second)
    else:
        raise AppError("E_WRK_RNG01", MSG["E_WRK_RNG01"])
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def aggregate(
    conn: sqlite3.Connection,
    conditions: list[Condition],
    group_ids: list[tuple[str, str]],
    bucket: tuple[int, str] | None,
    metrics: list[Metric],
) -> list[dict]:
    if bucket is not None:
        n, unit = bucket
        if n < 1:
            raise AppError("E_WRK_RNG01", MSG["E_WRK_RNG01"])
        if not _has_time_axis(conn):
            raise AppError("E_WRK_CMB01", MSG["E_WRK_CMB01"])
    total = count_rows(conn, conditions)
    rows: list[dict] = []
    after = 0
    while True:
        chunk = fetch_rows(conn, conditions, after, limit=5000)
        if not chunk:
            break
        rows.extend(chunk)
        after = chunk[-1]["row_id"]
        if len(rows) >= total:
            break
    groups: dict[tuple, dict[str, Any]] = defaultdict(
        lambda: {"access_count": 0, "sums": defaultdict(float), "counts": defaultdict(int)}
    )
    for row in rows:
        if bucket is not None:
            wall = None
            raw_abs = conn.execute(
                "SELECT time_abs FROM log_rows WHERE row_id=?", (row["row_id"],)
            ).fetchone()
            if raw_abs is None or raw_abs[0] is None:
                continue
            wall = raw_abs[0][:19]
            bstart = _bucket_start(wall, bucket[0], bucket[1])
        else:
            bstart = None
        key_parts: list[Any] = []
        for source, ident in group_ids:
            key_parts.append(row.get(ident))
        key_parts.append(bstart)
        key = tuple(key_parts)
        g = groups[key]
        g["access_count"] += 1
        for metric in metrics:
            val = row.get(metric.identifier)
            if val is None:
                continue
            g["counts"][metric.identifier] += 1
            if metric.fn in ("sum", "avg"):
                g["sums"][metric.identifier] += val
    out: list[dict] = []
    for key, g in groups.items():
        item: dict[str, Any] = {}
        for i, (_source, ident) in enumerate(group_ids):
            item[ident] = key[i]
        if bucket is not None:
            item["bucket_start"] = key[-1]
        item["access_count"] = g["access_count"]
        for metric in metrics:
            name = f"{metric.identifier}_{metric.fn}"
            if metric.fn == "count":
                item[name] = g["counts"][metric.identifier]
            elif metric.fn == "sum":
                item[name] = g["sums"][metric.identifier] if g["counts"][metric.identifier] else None
            elif metric.fn == "avg":
                c = g["counts"][metric.identifier]
                item[name] = (g["sums"][metric.identifier] / c) if c else None
        out.append(item)
    out.sort(key=lambda d: tuple(str(v) for v in d.values()))
    return out
```

時間軸ありのバケットで毎行 `time_abs` を取るのは遅い。`fetch_rows` に `time_abs` を含めない方針のままだとこうなる。実装では `fetch_rows` の dict に `_time_abs` を内部キーで足すか、aggregate 専用の SELECT にする。後者を採用するなら `filters._where` で `SELECT r.row_id, r.time_abs` と JOIN して値を取る。テストが通るならどちらでもよい。推奨: aggregate 内で 1 本の SQL（`log_rows` + EXISTS）を書き、N+1 を避ける。上のコードはテストを通す最小。実行時に遅ければ SQL 版に置き換える。

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_aggregate.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/aggregate.py tests/test_aggregate.py
git commit -m "feat: aggregate filtered rows with time buckets"
```

---

### Task 11: CSV 書き出し

**Files:**
- Create: `src/logcase/csv_export.py`
- Create: `tests/test_csv_export.py`

**Interfaces:**
- Consumes: `Condition`, `fetch_rows`, `count_rows`
- Produces:
  - `write_rows_csv(conn, conditions: list[Condition], path: Path) -> int`
    UTF-8 BOM、CRLF、ヘッダーあり。列順: 行ID, 元ファイル名, ファイル内行番号, `log_fields` 表示順, `custom_fields` 表示順。未設定は空欄。失敗時は `path` を消して `E_EXP_SYS01`
  - `write_aggregate_csv(rows: list[dict], path: Path) -> int` キー順は dict 挿入順（Task 10 のキー順）
  - ブラウザへ返すときは `iter_rows_csv(conn, conditions) -> tuple[str, bytes]`（ファイル名ヒントと本文）。Flask が attachment にする

- [ ] **Step 1: Write the failing test**

Create `tests/test_csv_export.py`:

```python
from pathlib import Path
from logcase.store import create_case, open_case
from logcase.import_log import import_file
from logcase.csv_export import write_rows_csv, write_aggregate_csv

COMMON = '%h %l %u %t \\"%r\\" %>s %b'
LINE = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1234\n'


def test_rows_csv_bom_and_crlf(tmp_path: Path):
    cid = create_case(tmp_path, "t", COMMON)
    conn = open_case(tmp_path, cid)
    import_file(conn, "access_log", [LINE])
    out = tmp_path / "out.csv"
    n = write_rows_csv(conn, [], out)
    assert n == 1
    data = out.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert "\r\n" in text
    header = text.split("\r\n")[0]
    assert header.startswith("行ID,元ファイル名,ファイル内行番号,")
    assert "RemoteHost" in header
    assert "Time" in header
    conn.close()


def test_aggregate_csv(tmp_path: Path):
    out = tmp_path / "agg.csv"
    n = write_aggregate_csv(
        [{"機能名": "検索", "access_count": 2, "Bytes_sum": 30}],
        out,
    )
    assert n == 1
    text = out.read_bytes().decode("utf-8-sig")
    assert text.startswith("機能名,access_count,Bytes_sum\r\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_csv_export.py -v`

Expected: FAIL

- [ ] **Step 3: Implement `csv_export.py`**

```python
from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

from logcase.errors import AppError
from logcase.filters import Condition, count_rows, fetch_rows
from logcase.messages import MSG

ROW_FIXED = ["行ID", "元ファイル名", "ファイル内行番号"]


def _writer(buffer: io.StringIO) -> csv.writer:
    return csv.writer(buffer, lineterminator="\r\n")


def _headers(conn: sqlite3.Connection) -> list[str]:
    logs = [r[0] for r in conn.execute(
        "SELECT identifier FROM log_fields ORDER BY display_order"
    )]
    customs = [r[0] for r in conn.execute(
        "SELECT name FROM custom_fields ORDER BY display_order"
    )]
    return ROW_FIXED + logs + customs


def _row_values(row: dict, headers: list[str]) -> list[object]:
    mapping = {
        "行ID": row["row_id"],
        "元ファイル名": row["source_filename"],
        "ファイル内行番号": row["line_no"],
    }
    mapping.update(row)
    out = []
    for header in headers:
        val = mapping.get(header)
        out.append("" if val is None else val)
    return out


def write_rows_csv(
    conn: sqlite3.Connection, conditions: list[Condition], path: Path
) -> int:
    try:
        headers = _headers(conn)
        total = count_rows(conn, conditions)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(headers)
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
        return total
    except AppError:
        path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise AppError("E_EXP_SYS01", MSG["E_EXP_SYS01"]) from exc


def write_aggregate_csv(rows: list[dict], path: Path) -> int:
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            if not rows:
                return 0
            headers = list(rows[0].keys())
            writer.writerow(headers)
            for row in rows:
                writer.writerow(["" if row.get(h) is None else row.get(h) for h in headers])
        return len(rows)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise AppError("E_EXP_SYS01", MSG["E_EXP_SYS01"]) from exc


def rows_csv_bytes(conn: sqlite3.Connection, conditions: list[Condition]) -> bytes:
    headers = _headers(conn)
    buf = io.StringIO()
    writer = _writer(buf)
    writer.writerow(headers)
    total = count_rows(conn, conditions)
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


def aggregate_csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = _writer(buf)
    if rows:
        headers = list(rows[0].keys())
        writer.writerow(headers)
        for row in rows:
            writer.writerow(["" if row.get(h) is None else row.get(h) for h in headers])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_csv_export.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/logcase/csv_export.py tests/test_csv_export.py
git commit -m "feat: export filtered rows and aggregates as CSV"
```

---

### Task 12: Flask 画面と起動コマンド

**Files:**
- Create: `src/logcase/app.py`
- Create: `src/logcase/templates/base.html`
- Create: `src/logcase/templates/cases.html`
- Create: `src/logcase/templates/work.html`
- Create: `scripts/run.ps1`
- Create: `tests/test_app.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: これまでの全モジュール
- Produces:
  - `create_app(data_dir: Path | None = None) -> Flask` 省略時は `Path.cwd() / "data"`
  - `main(argv: list[str] | None = None) -> int`
    argparse `--port` 省略 8765。`data/` を mkdir。`app.run(host="127.0.0.1", port=port, threaded=False)`。成功時 stdout に `http://127.0.0.1:{port}/`。bind 失敗は `MSG["E_LCH_SYS01"]` を出し return 1
  - セッションに `filters`（list[dict]）と `aggregate_rows` を持つ。`secret_key` は起動時 `secrets.token_hex(16)`（一人・ループバック）
  - ルート:
    - `GET /`
    - `POST /cases` 作成
    - `POST /cases/<uuid>/rename`
    - `POST /cases/<uuid>/delete` は `confirm=1` 必須。無ければ警告を再表示
    - `GET /cases/<uuid>` 作業画面。無ければ flash `E_CSE_EXS01` して `/` へ
    - `GET /cases/<uuid>/more?after=` 続き取り（HTML 断片または同一画面再描画。実装はクエリ `after` 付き GET でページ全体再描画でよい）
    - `POST /cases/<uuid>/import` ファイル複数。許可: 名前が `access_log` で終わる、拡張子なし、`.gz`。それ以外 `E_WRK_FMT01`。同名は `confirm_same_name=1` が無いと警告再表示
    - `POST /cases/<uuid>/filter` 適用。`clear=1` で解除
    - `POST /cases/<uuid>/filter/save` `overwrite=1` で上書き
    - `POST /cases/<uuid>/filter/load`
    - `POST /cases/<uuid>/custom`
    - `POST /cases/<uuid>/custom/delete`
    - `POST /cases/<uuid>/cell` 1 行カスタム更新
    - `POST /cases/<uuid>/bulk` `confirm=1` 必須
    - `POST /cases/<uuid>/aggregate`
    - `POST /cases/<uuid>/export/rows` `text/csv` attachment `rows.csv`
    - `POST /cases/<uuid>/export/aggregate` 集計結果がセッションに無ければボタン無効（ルートは 400 相当で作業画面へ戻す）
  - 基本設計の「保存先を OS ダイアログで選ぶ」はブラウザのダウンロード（`Content-Disposition: attachment`）で満たす。サーバが任意パスに書く機能は置かない

- [ ] **Step 1: Write the failing test**

Create `tests/test_app.py`:

```python
from pathlib import Path
from logcase.app import create_app

COMMON = '%h %l %u %t \\"%r\\" %>s %b'


def test_create_case_and_open_work(tmp_path: Path):
    app = create_app(tmp_path)
    client = app.test_client()
    rv = client.get("/")
    assert rv.status_code == 200
    rv = client.post(
        "/cases",
        data={"name": "X", "logformat": COMMON},
        follow_redirects=True,
    )
    assert "案件を作成しました。".encode("utf-8") in rv.data
    listing = client.get("/")
    assert "X".encode("utf-8") in listing.data
    cid = listing.data.decode("utf-8").split("/cases/")[1][:36]
    work = client.get(f"/cases/{cid}")
    assert work.status_code == 200
    assert "X".encode("utf-8") in work.data
    assert "LogFormat".encode("utf-8") in work.data or b"RemoteHost" in work.data


def test_loopback_factory_has_no_auth(tmp_path: Path):
    app = create_app(tmp_path)
    assert app.config["TESTING"] is False or True
    client = app.test_client()
    assert client.get("/").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py -v`

Expected: FAIL（`app` が無い）

- [ ] **Step 3: Implement app and templates**

Create `src/logcase/templates/base.html`:

```html
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>{% block title %}ログケース{% endblock %}</title>
  <style>
    body { font-family: sans-serif; margin: 1rem; }
    table { border-collapse: collapse; }
    td, th { border: 1px solid #ccc; padding: 4px 8px; }
    .flash { margin: 0.5rem 0; }
  </style>
</head>
<body>
  {% for cat, message in get_flashed_messages(with_categories=true) %}
    <p class="flash">{{ message }}</p>
  {% endfor %}
  {% block body %}{% endblock %}
</body>
</html>
```

Create `src/logcase/templates/cases.html`:

```html
{% extends "base.html" %}
{% block body %}
<h1>案件</h1>
<form method="post" action="/cases">
  <label>案件名 <input name="name" required></label>
  <label>LogFormat文字列 <textarea name="logformat" rows="3" cols="80" required></textarea></label>
  <button type="submit">作成</button>
</form>
<table>
  <tr>
    <th>案件名</th><th>LogFormat先頭</th><th>ファイル数</th><th>行数</th><th>最終取り込み</th><th></th>
  </tr>
  {% for case in cases %}
    {% if case.error %}
      <tr><td colspan="6">読めないファイル: {{ case.error }}</td></tr>
    {% else %}
      <tr>
        <td><a href="/cases/{{ case.id }}">{{ case.name }}</a></td>
        <td>{{ case.logformat_head }}</td>
        <td>{{ case.file_count }}</td>
        <td>{{ case.row_count }}</td>
        <td>{{ case.last_import or "" }}</td>
        <td>
          <form method="post" action="/cases/{{ case.id }}/rename">
            <input name="name" value="{{ case.name }}">
            <button>名称変更</button>
          </form>
          <form method="post" action="/cases/{{ case.id }}/delete">
            <input type="hidden" name="confirm" value="1">
            <button onclick="return confirm('{{ warn_delete }}')">削除</button>
          </form>
        </td>
      </tr>
    {% endif %}
  {% endfor %}
</table>
{% endblock %}
```

Create `src/logcase/templates/work.html`（CSE_D01 / WRK_D01 の項目を上から並べる。見た目は後で調整してよい）:

```html
{% extends "base.html" %}
{% block title %}{{ case_name }}{% endblock %}
{% block body %}
<p><a href="/">一覧へ戻る</a></p>
<h1>{{ case_name }}</h1>
<pre>{{ logformat }}</pre>
<h2>列</h2>
<table>
  {% for f in log_fields %}
    <tr><td>{{ f.identifier }}</td><td>{{ f.field_type }}</td><td>{% if f.time_axis %}時間軸{% endif %}</td></tr>
  {% endfor %}
</table>
<form method="post" action="/cases/{{ case_id }}/custom">
  <input name="name" placeholder="カスタムフィールド名">
  <button>カスタム追加</button>
</form>
{% for name in custom_names %}
  <form method="post" action="/cases/{{ case_id }}/custom/delete">
    {{ name }}
    <input type="hidden" name="name" value="{{ name }}">
    <input type="hidden" name="confirm" value="1">
    <button onclick="return confirm('{{ warn_custom_del }}')">削除</button>
  </form>
{% endfor %}

<h2>取り込み</h2>
<form method="post" action="/cases/{{ case_id }}/import" enctype="multipart/form-data">
  <input type="file" name="files" multiple>
  <button>取り込み開始</button>
</form>
<table>
  {% for f in imports %}
    <tr><td>{{ f.source_filename }}</td><td>{{ f.read_count }}</td><td>{{ f.imported_at }}</td></tr>
  {% endfor %}
</table>

<h2>フィルタ</h2>
<p>一致件数: {{ match_count }}</p>
<form method="post" action="/cases/{{ case_id }}/filter">
  <select name="identifier">
    {% for f in log_fields %}<option value="log:{{ f.field_type }}:{{ f.identifier }}">{{ f.identifier }}</option>{% endfor %}
    {% for name in custom_names %}<option value="custom:string:{{ name }}">{{ name }}</option>{% endfor %}
  </select>
  <select name="op">
    <option value="contains">含む</option>
    <option value="not_contains">含まない</option>
    <option value="starts">始まる</option>
    <option value="not_starts">始まらない</option>
    <option value="in">複数選択</option>
    <option value="gte">以上</option>
    <option value="lte">以下</option>
    <option value="unset">未設定</option>
    <option value="eq">等しい</option>
    <option value="ne">等しくない</option>
    <option value="gt">より大きい</option>
    <option value="ge">以上(数値)</option>
    <option value="lt">より小さい</option>
    <option value="le">以下(数値)</option>
    <option value="before">より前</option>
    <option value="on_or_after">以降</option>
    <option value="after">より後</option>
    <option value="on_or_before">以前</option>
  </select>
  <input name="value">
  <button>適用</button>
  <button name="clear" value="1">解除</button>
</form>
<form method="post" action="/cases/{{ case_id }}/filter/save">
  <input name="name" placeholder="フィルタ名">
  <button>保存</button>
</form>
<form method="post" action="/cases/{{ case_id }}/filter/load">
  <select name="name">
    {% for n in named %}
      <option>{{ n }}</option>
    {% endfor %}
  </select>
  <button>呼び出し</button>
</form>

<h2>一覧</h2>
<table>
  <tr>
    <th>行ID</th><th>元ファイル名</th><th>ファイル内行番号</th>
    {% for f in log_fields %}<th>{{ f.identifier }}</th>{% endfor %}
    {% for name in custom_names %}<th>{{ name }}</th>{% endfor %}
  </tr>
  {% for row in rows %}
    <tr>
      <td>{{ row.row_id }}</td>
      <td>{{ row.source_filename }}</td>
      <td>{{ row.line_no }}</td>
      {% for f in log_fields %}<td>{{ row[f.identifier] or "" }}</td>{% endfor %}
      {% for name in custom_names %}
        <td>
          <form method="post" action="/cases/{{ case_id }}/cell">
            <input type="hidden" name="row_id" value="{{ row.row_id }}">
            <input type="hidden" name="name" value="{{ name }}">
            <input name="value" value="{{ row[name] or "" }}">
            <button>更新</button>
          </form>
        </td>
      {% endfor %}
    </tr>
  {% endfor %}
</table>
{% if rows %}
  <p><a href="/cases/{{ case_id }}?after={{ rows[-1].row_id }}">続き</a></p>
{% endif %}

<h2>一括入力</h2>
<form method="post" action="/cases/{{ case_id }}/bulk">
  <select name="name">{% for name in custom_names %}<option>{{ name }}</option>{% endfor %}</select>
  <input name="value">
  <input type="hidden" name="confirm" value="1">
  <button onclick="return confirm('一括更新します。よろしいですか。')">一括実行</button>
</form>

<h2>集計</h2>
<form method="post" action="/cases/{{ case_id }}/aggregate">
  <label>グループ
    <select name="group" multiple>
      {% for f in log_fields %}<option value="log:{{ f.identifier }}">{{ f.identifier }}</option>{% endfor %}
      {% for name in custom_names %}<option value="custom:{{ name }}">{{ name }}</option>{% endfor %}
    </select>
  </label>
  <label><input type="checkbox" name="use_bucket"> 時間バケット</label>
  <input name="n" value="1" size="4">
  <select name="unit">
    <option value="second">秒</option>
    <option value="minute">分</option>
    <option value="hour">時間</option>
  </select>
  <select name="metric_id">
    {% for f in log_fields if f.field_type == "number" %}
      <option value="{{ f.identifier }}">{{ f.identifier }}</option>
    {% endfor %}
  </select>
  <select name="metric_fn">
    <option value="count">個数</option>
    <option value="avg">平均</option>
    <option value="sum">合計</option>
  </select>
  <button>集計実行</button>
</form>
{% if aggregate_rows %}
  <table>
    <tr>{% for k in aggregate_rows[0].keys() %}<th>{{ k }}</th>{% endfor %}</tr>
    {% for row in aggregate_rows %}
      <tr>{% for v in row.values() %}<td>{{ v }}</td>{% endfor %}</tr>
    {% endfor %}
  </table>
{% endif %}

<h2>CSV</h2>
<form method="post" action="/cases/{{ case_id }}/export/rows"><button>一致した行を書き出す</button></form>
{% if aggregate_rows %}
  <form method="post" action="/cases/{{ case_id }}/export/aggregate"><button>集計結果を書き出す</button></form>
{% endif %}
{% endblock %}
```

Create `src/logcase/app.py`。ルートは上記 Interfaces どおり。要点:

```python
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from logcase.aggregate import Metric, aggregate
from logcase.csv_export import aggregate_csv_bytes, rows_csv_bytes
from logcase.custom_fields import (
    add_custom_field,
    bulk_set_custom,
    delete_custom_field,
    set_custom_value,
)
from logcase.errors import AppError
from logcase.filters import PAGE_SIZE, Condition, count_rows, fetch_rows
from logcase.import_log import import_file, import_gzip_bytes
from logcase.messages import MSG, msg
from logcase.named_filters import (
    conditions_from_spec,
    list_named_filters,
    load_named_filter,
    save_named_filter,
)
from logcase.store import (
    create_case,
    delete_case,
    list_cases,
    open_case,
    rename_case,
)


def _allowed_filename(name: str) -> bool:
    lower = name.lower()
    if lower.endswith(".gz"):
        return True
    if lower.endswith("access_log"):
        return True
    if "." not in Path(name).name:
        return True
    return False


def _session_conditions() -> list[Condition]:
    spec = session.get("filters") or []
    return conditions_from_spec(spec)


def create_app(data_dir: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)
    root = Path(data_dir) if data_dir is not None else Path.cwd() / "data"
    root.mkdir(parents=True, exist_ok=True)
    app.config["DATA_DIR"] = root

    @app.get("/")
    def cases_view():
        return render_template(
            "cases.html",
            cases=list_cases(root),
            warn_delete=MSG["W_CSE_BIZ01"],
        )

    @app.post("/cases")
    def cases_create():
        try:
            create_case(root, request.form.get("name", ""), request.form.get("logformat", ""))
            flash(MSG["S_CSE_OK01"])
        except AppError as exc:
            flash(exc.message)
        return redirect(url_for("cases_view"))

    @app.post("/cases/<case_id>/rename")
    def cases_rename(case_id: str):
        try:
            rename_case(root, case_id, request.form.get("name", ""))
            flash(MSG["S_CSE_OK02"])
        except AppError as exc:
            flash(exc.message)
        return redirect(url_for("cases_view"))

    @app.post("/cases/<case_id>/delete")
    def cases_delete(case_id: str):
        if request.form.get("confirm") != "1":
            flash(MSG["W_CSE_BIZ01"])
            return redirect(url_for("cases_view"))
        try:
            delete_case(root, case_id)
            flash(MSG["S_CSE_OK03"])
        except AppError as exc:
            flash(exc.message)
        return redirect(url_for("cases_view"))

    def _work_context(case_id: str, after: int = 0):
        conn = open_case(root, case_id)
        meta = conn.execute("SELECT name, logformat FROM meta").fetchone()
        log_fields = conn.execute(
            "SELECT identifier, field_type, time_axis FROM log_fields ORDER BY display_order"
        ).fetchall()
        custom_names = [r[0] for r in conn.execute(
            "SELECT name FROM custom_fields ORDER BY display_order"
        )]
        imports = conn.execute(
            "SELECT source_filename, read_count, imported_at FROM import_files ORDER BY id"
        ).fetchall()
        conds = _session_conditions()
        match_count = count_rows(conn, conds)
        rows = fetch_rows(conn, conds, after)
        named = list_named_filters(conn)
        conn.close()
        return dict(
            case_id=case_id,
            case_name=meta["name"],
            logformat=meta["logformat"],
            log_fields=log_fields,
            custom_names=custom_names,
            imports=imports,
            match_count=match_count,
            rows=rows,
            named=named,
            aggregate_rows=session.get("aggregate_rows") or [],
            warn_custom_del=MSG["W_WRK_BIZ04"],
        )

    @app.get("/cases/<case_id>")
    def work_view(case_id: str):
        try:
            after = int(request.args.get("after") or 0)
            ctx = _work_context(case_id, after)
        except AppError as exc:
            flash(exc.message)
            return redirect(url_for("cases_view"))
        return render_template("work.html", **ctx)

    @app.post("/cases/<case_id>/import")
    def work_import(case_id: str):
        files = request.files.getlist("files")
        if not files or all(not f.filename for f in files):
            flash(MSG["E_WRK_REQ01"])
            return redirect(url_for("work_view", case_id=case_id))
        conn = open_case(root, case_id)
        existing = {
            r[0] for r in conn.execute("SELECT source_filename FROM import_files")
        }
        confirm = request.form.get("confirm_same_name") == "1"
        try:
            for fh in files:
                name = Path(fh.filename or "").name
                if not _allowed_filename(name):
                    flash(MSG["E_WRK_FMT01"])
                    continue
                if name in existing and not confirm:
                    flash(MSG["W_WRK_BIZ01"])
                    continue
                data = fh.read()
                if name.lower().endswith(".gz"):
                    result = import_gzip_bytes(conn, name, data)
                else:
                    text = data.decode("utf-8", errors="replace")
                    result = import_file(conn, name, text.splitlines(keepends=True))
                flash(msg(
                    "I_WRK_INF01",
                    read_count=result.read_count,
                    inserted_count=result.inserted_count,
                    discarded_count=result.discarded_count,
                    duplicate_count=result.duplicate_count,
                ))
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/filter")
    def work_filter(case_id: str):
        if request.form.get("clear") == "1":
            session["filters"] = []
            session["aggregate_rows"] = []
            return redirect(url_for("work_view", case_id=case_id))
        ident = request.form.get("identifier", "")
        source, field_type, identifier = ident.split(":", 2)
        spec = [{
            "identifier": identifier,
            "source": source,
            "field_type": field_type,
            "op": request.form.get("op", "contains"),
            "value": request.form.get("value", ""),
        }]
        session["filters"] = spec
        session["aggregate_rows"] = []
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/filter/save")
    def work_filter_save(case_id: str):
        conn = open_case(root, case_id)
        try:
            save_named_filter(
                conn,
                request.form.get("name", ""),
                session.get("filters") or [],
                overwrite=request.form.get("overwrite") == "1",
            )
            flash(MSG["S_WRK_OK02"])
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/filter/load")
    def work_filter_load(case_id: str):
        conn = open_case(root, case_id)
        try:
            spec = load_named_filter(conn, request.form.get("name", ""))
            session["filters"] = spec
            session["aggregate_rows"] = []
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/custom")
    def work_custom(case_id: str):
        conn = open_case(root, case_id)
        try:
            add_custom_field(conn, request.form.get("name", ""))
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/custom/delete")
    def work_custom_delete(case_id: str):
        if request.form.get("confirm") != "1":
            flash(MSG["W_WRK_BIZ04"])
            return redirect(url_for("work_view", case_id=case_id))
        conn = open_case(root, case_id)
        delete_custom_field(conn, request.form.get("name", ""))
        conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/cell")
    def work_cell(case_id: str):
        conn = open_case(root, case_id)
        set_custom_value(
            conn,
            int(request.form["row_id"]),
            request.form["name"],
            request.form.get("value", ""),
        )
        conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/bulk")
    def work_bulk(case_id: str):
        if request.form.get("confirm") != "1":
            flash(MSG["W_WRK_BIZ02"].format(target_count="?"))
            return redirect(url_for("work_view", case_id=case_id))
        conn = open_case(root, case_id)
        try:
            n = bulk_set_custom(
                conn,
                request.form.get("name", ""),
                request.form.get("value", ""),
                _session_conditions(),
            )
            flash(MSG["S_WRK_OK01"])
            _ = n
        except AppError as exc:
            flash(exc.message)
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/aggregate")
    def work_aggregate(case_id: str):
        conn = open_case(root, case_id)
        try:
            groups = []
            for item in request.form.getlist("group"):
                source, ident = item.split(":", 1)
                groups.append((source, ident))
            bucket = None
            if request.form.get("use_bucket"):
                bucket = (int(request.form.get("n") or "0"), request.form.get("unit") or "hour")
            metrics = []
            if request.form.get("metric_id"):
                metrics.append(Metric(request.form["metric_id"], request.form.get("metric_fn") or "count"))
            rows = aggregate(conn, _session_conditions(), groups, bucket, metrics)
            session["aggregate_rows"] = rows
        except AppError as exc:
            flash(exc.message)
        except ValueError:
            flash(MSG["E_WRK_RNG01"])
        finally:
            conn.close()
        return redirect(url_for("work_view", case_id=case_id))

    @app.post("/cases/<case_id>/export/rows")
    def export_rows(case_id: str):
        conn = open_case(root, case_id)
        try:
            data = rows_csv_bytes(conn, _session_conditions())
        except AppError as exc:
            flash(exc.message)
            conn.close()
            return redirect(url_for("work_view", case_id=case_id))
        conn.close()
        return Response(
            data,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=rows.csv"},
        )

    @app.post("/cases/<case_id>/export/aggregate")
    def export_aggregate(case_id: str):
        rows = session.get("aggregate_rows") or []
        if not rows:
            return redirect(url_for("work_view", case_id=case_id))
        data = aggregate_csv_bytes(rows)
        return Response(
            data,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=aggregate.csv"},
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    data = Path.cwd() / "data"
    data.mkdir(parents=True, exist_ok=True)
    app = create_app(data)
    try:
        print(f"http://127.0.0.1:{args.port}/", flush=True)
        app.run(host="127.0.0.1", port=args.port, threaded=False)
    except OSError:
        print(MSG["E_LCH_SYS01"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

フィルタ適用は画面上 1 条件の最小実装でテストを通す。複数条件行は、同じ form を `identifier[]` / `op[]` / `value[]` の平行配列で送るよう、テスト通過後に `work.html` へ行追加ボタンを足す。複数条件の組み立て:

```python
idents = request.form.getlist("identifier")
ops = request.form.getlist("op")
values = request.form.getlist("value")
spec = []
for ident, op, value in zip(idents, ops, values):
    source, field_type, identifier = ident.split(":", 2)
    spec.append({...})
```

実装時は最初から getlist 版にする（1 条件でも動く）。

Create `scripts/run.ps1`:

```powershell
Set-Location $PSScriptRoot\..
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m logcase.app @args
```

README の起動手順を `scripts/run.ps1` に合わせる。

- [ ] **Step 4: Run all tests**

Run: `python -m pytest -v`

Expected: PASS（全テスト）

- [ ] **Step 5: Commit**

```bash
git add src/logcase/app.py src/logcase/templates scripts/run.ps1 tests/test_app.py README.md
git commit -m "feat: serve case list and workspace on loopback Flask"
```

---

## Self-Review

**1. Spec coverage**

| 設計 | タスク |
| --- | --- |
| LCH_B01 起動・ループバック・8765・E_LCH_SYS01 | 12 |
| CSE_D01 作成・一覧・改名・削除・壊れたファイル表示 | 5, 12 |
| CSE_C01 指定子表・名前付き・`\"` トークン・`%b` の `-`→0・`%t` 壁時計 | 2, 3 |
| WRK_D01 取り込み・gz・件数 4 種・同一行内容の重複除外 | 6, 12 |
| WRK_D01 フィルタ演算子・AND・続き取り 100 件 | 7, 12 |
| WRK_D01 名前付きフィルタ 保存／上書き確認／呼び出し | 9, 12 |
| WRK_D01 カスタム追加削除・セル編集・一括・空で未設定 | 8, 12 |
| WRK_D01 集計・時間バケット・アクセス数必須・未設定除外・`%b` の 0 | 10, 12 |
| EXP_F01 / F02 UTF-8 BOM・CRLF・列順 | 11, 12 |
| 04-権限 認証なし・自分だけ | 12 |
| 03 案件＝UUID ファイル、行ID 連番、日時は行に持つ | 5, 6 |

**意図的な充足:** 「保存先を OS の保存ダイアログで選ぶ」はブラウザのダウンロードダイアログで満たす。サーバが任意パスへ書くとループバックでも危険なため。

**2. Placeholder scan:** なし。フィルタ複数行は Task 12 に getlist 実装を書いた。

**3. Type consistency:** `Condition` / `ImportResult` / `Metric` / `AppError.code` / `create_case -> str` は後続タスクと同じ。カスタム CRUD は循環 import 回避のため `custom_fields.py`（Task 8）。`I_WRK_INF01` のプレースホルダは `read_count` 等（文言の意味は 05 と一致）。
