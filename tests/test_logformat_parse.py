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


def _by_id(parsed):
    return {v.identifier: v for v in parsed.values}


def test_split_request_common_line():
    c = compile_logformat(COMMON)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /search?q=foo&p=1 HTTP/1.1" 200 1234'
    parsed = parse_line(c, line)
    assert parsed is not None
    by_id = _by_id(parsed)
    assert by_id["Request"].str_value == "GET /search?q=foo&p=1 HTTP/1.1"
    assert by_id["Request-Method"].str_value == "GET"
    assert by_id["Request-Path"].str_value == "/search"
    assert by_id["Request-Query"].str_value == "q=foo&p=1"
    assert by_id["Request-Protocol"].str_value == "HTTP/1.1"
    assert by_id["Request-Query"].is_unset is False


def test_split_request_empty_query_is_empty_string():
    c = compile_logformat(COMMON)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1'
    by_id = _by_id(parse_line(c, line))
    assert by_id["Request-Path"].str_value == "/index"
    assert by_id["Request-Query"].is_unset is False
    assert by_id["Request-Query"].str_value == ""
    assert by_id["Request-Protocol"].str_value == "HTTP/1.1"


def test_split_request_http09_protocol_unset():
    from logcase.logformat import split_request

    values = {v.identifier: v for v in split_request("GET /index")}
    assert values["Request-Method"].str_value == "GET"
    assert values["Request-Path"].str_value == "/index"
    assert values["Request-Query"].str_value == ""
    assert values["Request-Protocol"].is_unset is True
    assert values["Request-Protocol"].str_value is None


def test_split_request_absolute_url():
    from logcase.logformat import split_request

    values = {v.identifier: v for v in split_request("GET http://host/foo?x=1 HTTP/1.1")}
    assert values["Request-Method"].str_value == "GET"
    assert values["Request-Path"].str_value == "http://host/foo"
    assert values["Request-Query"].str_value == "x=1"
    assert values["Request-Protocol"].str_value == "HTTP/1.1"


def test_derived_values_follow_request_in_order():
    from logcase.logformat import REQUEST_DERIVED_IDS

    c = compile_logformat(COMMON)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "GET /index HTTP/1.1" 200 1'
    parsed = parse_line(c, line)
    assert parsed is not None
    ids = [v.identifier for v in parsed.values]
    request_at = ids.index("Request")
    assert ids[request_at + 1 : request_at + 1 + len(REQUEST_DERIVED_IDS)] == list(
        REQUEST_DERIVED_IDS
    )


def test_split_request_dash_all_unset():
    from logcase.logformat import split_request

    values = split_request("-")
    assert [v.identifier for v in values] == [
        "Request-Method",
        "Request-Path",
        "Request-Query",
        "Request-Protocol",
    ]
    assert all(v.is_unset and v.str_value is None for v in values)


def test_parse_dash_request_keeps_request_and_unsets_derived():
    c = compile_logformat(COMMON)
    line = '127.0.0.1 - - [10/Oct/2024:12:00:00 +0900] "-" 400 0'
    parsed = parse_line(c, line)
    assert parsed is not None
    by_id = _by_id(parsed)
    assert by_id["Request"].str_value == "-"
    assert by_id["Request"].is_unset is False
    assert by_id["Request-Method"].is_unset is True
