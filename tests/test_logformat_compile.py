import pytest
from access_log_db.errors import LogFormatError
from access_log_db.logformat import compile_logformat


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
        "Request-Method",
        "Request-Path",
        "Request-Query",
        "Request-Protocol",
        "Status",
        "Bytes",
    ]
    time_f = compiled.fields[3]
    assert time_f.field_type == "datetime"
    assert time_f.time_axis is True
    status = compiled.fields[9]
    assert status.key == "s"
    assert status.identifier == "Status"
    derived = compiled.fields[5]
    assert derived.identifier == "Request-Method"
    assert derived.field_type == "string"
    assert derived.specifier == "derived:%r"
    assert derived.key == "derived"
    assert derived.derived is True
    assert compiled.fields[4].derived is False


def test_named_header_identifier_preserves_case():
    compiled = compile_logformat('%h \\"%{User-Agent}i\\"')
    assert compiled.fields[1].identifier == "User-Agent"
    assert compiled.fields[1].field_type == "string"


def test_no_request_field_has_no_derived_columns():
    compiled = compile_logformat("%h %t %s")
    assert [f.identifier for f in compiled.fields] == [
        "RemoteHost",
        "Time",
        "Status",
    ]


def test_named_request_header_is_not_decomposed():
    compiled = compile_logformat('%h \\"%{Request}i\\"')
    assert [f.identifier for f in compiled.fields] == ["RemoteHost", "Request"]


def test_named_r_specifier_is_not_decomposed():
    compiled = compile_logformat('%{Request}r')
    assert [f.identifier for f in compiled.fields] == ["Request"]


def test_derived_identifier_collision_raises_dup01():
    with pytest.raises(LogFormatError) as ei:
        compile_logformat('%r \\"%{Request-Path}i\\"')
    assert ei.value.code == "E_CSE_DUP01"


def test_include_derived_false_allows_request_path_name():
    compiled = compile_logformat(
        '%r \\"%{Request-Path}i\\"', include_derived=False
    )
    assert [f.identifier for f in compiled.fields] == ["Request", "Request-Path"]


def test_greater_than_r_inserts_derived_after_request():
    compiled = compile_logformat("%>r")
    assert [f.identifier for f in compiled.fields] == [
        "Request",
        "Request-Method",
        "Request-Path",
        "Request-Query",
        "Request-Protocol",
    ]


def test_bare_i_rejected():
    with pytest.raises(LogFormatError) as ei:
        compile_logformat("%i")
    assert ei.value.code == "E_CSE_FMT02"


def test_invalid_regex_literal_raises_fmt02():
    with pytest.raises(LogFormatError) as ei:
        compile_logformat("(")
    assert ei.value.code == "E_CSE_FMT02"
