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
