from access_log_db.app import listen_host


def test_listen_host_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("ACCESS_LOG_BIND", raising=False)
    assert listen_host() == "127.0.0.1"


def test_listen_host_allows_all_interfaces_for_container(monkeypatch):
    monkeypatch.setenv("ACCESS_LOG_BIND", "0.0.0.0")
    assert listen_host() == "0.0.0.0"


def test_listen_host_rejects_unknown(monkeypatch):
    monkeypatch.setenv("ACCESS_LOG_BIND", "1.2.3.4")
    assert listen_host() == "127.0.0.1"
