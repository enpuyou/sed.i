import app.main as main


def test_health_is_liveness_only(client):
    assert client.get("/health").json() == {"status": "healthy"}


def test_ready_ok(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_ready_503_names_db(client, monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(main, "_check_db", boom)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "failed": "database"}
    assert client.get("/health").status_code == 200


def test_ready_503_names_redis(client, monkeypatch):
    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(main, "_check_redis", boom)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "failed": "redis"}


def test_ready_503_on_timeout(client, monkeypatch):
    import time

    monkeypatch.setattr(main, "_check_db", lambda: time.sleep(3))
    monkeypatch.setattr(main, "READY_TIMEOUT_SECONDS", 0.2)
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["failed"] == "database"
