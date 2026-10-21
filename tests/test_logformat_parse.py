from datetime import datetime, timezone, timedelta
from access_log_db.logformat import compile_logformat, parse_line

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
