from access_log_db import HEALTH


def test_health_constant():
    assert HEALTH == "ok"
