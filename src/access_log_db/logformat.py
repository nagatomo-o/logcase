from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

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

REQUEST_DERIVED_IDS = (
    "Request-Method",
    "Request-Path",
    "Request-Query",
    "Request-Protocol",
)

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
    derived: bool = False


@dataclass(frozen=True)
class CompiledFormat:
    fields: list[FieldDef]
    pattern: re.Pattern[str]


def compile_logformat(text: str, include_derived: bool = True) -> CompiledFormat:
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
                derived=False,
            )
        )
        pattern_src = pattern_src.replace(specifier, pat, 1)
    if include_derived and any(
        f.key == "r"
        and f.identifier == "Request"
        and "{" not in f.specifier
        and not f.derived
        for f in fields
    ):
        for ident in REQUEST_DERIVED_IDS:
            if ident in seen:
                raise LogFormatError(
                    "E_CSE_DUP01", "同じフィールド名が重複しているため登録できません。"
                )
            seen.add(ident)
        expanded: list[FieldDef] = []
        for field in fields:
            expanded.append(field)
            if (
                field.key == "r"
                and field.identifier == "Request"
                and "{" not in field.specifier
                and not field.derived
            ):
                for ident in REQUEST_DERIVED_IDS:
                    expanded.append(
                        FieldDef(
                            identifier=ident,
                            field_type="string",
                            specifier="derived:%r",
                            order=0,
                            time_axis=False,
                            key="derived",
                            derived=True,
                        )
                    )
        fields = [
            FieldDef(
                identifier=f.identifier,
                field_type=f.field_type,
                specifier=f.specifier,
                order=i,
                time_axis=f.time_axis,
                key=f.key,
                derived=f.derived,
            )
            for i, f in enumerate(expanded)
        ]
    try:
        compiled = re.compile(pattern_src, re.IGNORECASE)
    except re.error:
        raise LogFormatError(
            "E_CSE_FMT02", "LogFormatを解釈できません。指定子を確認してください。"
        )
    return CompiledFormat(fields=fields, pattern=compiled)


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


def _unset_derived() -> list[ParsedValue]:
    return [ParsedValue(ident, True, None, None) for ident in REQUEST_DERIVED_IDS]


def split_request(raw: str) -> list[ParsedValue]:
    if raw == "-":
        return _unset_derived()
    parts = raw.split()
    if not parts:
        return _unset_derived()
    method = parts[0]
    rest = parts[1:]
    protocol: str | None = None
    if rest and rest[-1].startswith("HTTP/"):
        protocol = rest[-1]
        target = " ".join(rest[:-1])
    else:
        target = " ".join(rest)
    if "?" in target:
        path, query = target.split("?", 1)
    else:
        path, query = target, ""
    method_id, path_id, query_id, protocol_id = REQUEST_DERIVED_IDS
    return [
        ParsedValue(method_id, False, method, None),
        ParsedValue(path_id, False, path, None),
        ParsedValue(query_id, False, query, None),
        ParsedValue(protocol_id, protocol is None, protocol, None),
    ]


def parse_line(compiled: CompiledFormat, line: str) -> Optional[ParsedLine]:
    if line == "":
        return None
    escaped = line.replace('\\"', TEMP_QUOTE)
    capture_fields = [f for f in compiled.fields if not f.derived]
    match = compiled.pattern.match(escaped)
    if match is None or len(match.groups()) != len(capture_fields):
        return None
    values: list[ParsedValue] = []
    time_display = None
    time_abs = None
    time_offset = None
    for i, field in enumerate(capture_fields):
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
    request = next((v for v in values if v.identifier == "Request"), None)
    if request is not None and any(f.derived for f in compiled.fields):
        derived_values = split_request(request.str_value or "")
        insert_at = next(
            i for i, v in enumerate(values) if v.identifier == "Request"
        ) + 1
        values[insert_at:insert_at] = derived_values
    return ParsedLine(values, time_display, time_abs, time_offset)
