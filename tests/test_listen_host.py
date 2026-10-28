from logcase.app import extra_allowed_hosts, listen_host


def test_listen_host_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("ACCESS_LOG_BIND", raising=False)
    assert listen_host() == "127.0.0.1"


def test_listen_host_allows_all_interfaces_for_container(monkeypatch):
    monkeypatch.setenv("ACCESS_LOG_BIND", "0.0.0.0")
    assert listen_host() == "0.0.0.0"


def test_listen_host_rejects_unknown(monkeypatch):
    monkeypatch.setenv("ACCESS_LOG_BIND", "1.2.3.4")
    assert listen_host() == "127.0.0.1"


def test_extra_allowed_hosts_empty_by_default(monkeypatch):
    monkeypatch.delenv("ACCESS_LOG_HOST", raising=False)
    assert extra_allowed_hosts() == frozenset()


def test_extra_allowed_hosts_from_env(monkeypatch):
    monkeypatch.setenv("ACCESS_LOG_HOST", "DESKTOP-9JI8GNA")
    assert extra_allowed_hosts() == frozenset({"desktop-9ji8gna"})


def test_extra_allowed_hosts_comma_separated(monkeypatch):
    monkeypatch.setenv("ACCESS_LOG_HOST", "desktop-9ji8gna, other.local")
    assert extra_allowed_hosts() == frozenset({"desktop-9ji8gna", "other.local"})
