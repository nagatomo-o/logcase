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
