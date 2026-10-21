from __future__ import annotations

import re
from dataclasses import dataclass

from access_log_db.errors import LogFormatError

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
    try:
        compiled = re.compile(pattern_src, re.IGNORECASE)
    except re.error:
        raise LogFormatError(
            "E_CSE_FMT02", "LogFormatを解釈できません。指定子を確認してください。"
        )
    return CompiledFormat(fields=fields, pattern=compiled)
