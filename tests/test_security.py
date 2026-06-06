from fastapi.testclient import TestClient

import main
from thinktank.models import DebateRequest


def test_security_headers_present():
    client = TestClient(main.app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_validate_provider_rejects_unknown_model_before_provider_call(monkeypatch):
    called = False

    async def fake_validate_provider_key(payload):
        nonlocal called
        called = True
        return {"valid": True}

    monkeypatch.setattr(main, "validate_provider_key", fake_validate_provider_key)
    client = TestClient(main.app)

    response = client.post(
        "/api/validate-provider",
        json={"provider": "openai", "model": "not-a-real-listed-model"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported provider/model combination."
    assert called is False


def test_validate_provider_rate_limit(monkeypatch):
    async def fake_validate_provider_key(payload):
        return {"valid": True}

    monkeypatch.setattr(main, "validate_provider_key", fake_validate_provider_key)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(main.RATE_LIMITS, ("POST", "/api/validate-provider"), (2, 60))
    main._rate_limit_hits.clear()
    client = TestClient(main.app)
    payload = {"provider": "openai", "model": "gpt-4o"}

    assert client.post("/api/validate-provider", json=payload).status_code == 200
    assert client.post("/api/validate-provider", json=payload).status_code == 200
    limited = client.post("/api/validate-provider", json=payload)

    assert limited.status_code == 429
    assert limited.headers["Retry-After"]


def test_providers_returns_only_configured_openai_and_gemini(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = TestClient(main.app)

    empty = client.get("/api/providers")
    assert empty.status_code == 200
    assert empty.json()["providers"] == []

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    configured = client.get("/api/providers")

    assert configured.status_code == 200
    assert [provider["id"] for provider in configured.json()["providers"]] == ["gemini"]

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    both_configured = client.get("/api/providers")

    assert both_configured.status_code == 200
    assert [provider["id"] for provider in both_configured.json()["providers"]] == ["gemini", "openai"]


def test_debate_rejects_unconfigured_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = TestClient(main.app)

    response = client.post(
        "/api/debate",
        json={
            "topic": "Should AI govern cities?",
            "rounds": 1,
            "agents": [
                {
                    "id": "nova",
                    "name": "Nova",
                    "role": "Optimist",
                    "personality": "Sees constructive progress.",
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                },
                {
                    "id": "rex",
                    "name": "Rex",
                    "role": "Skeptic",
                    "personality": "Questions hidden risks.",
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                },
            ],
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "gemini is not configured on this server."


def test_judgeai_rejects_missing_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(main.app)

    response = client.post(
        "/api/judgeai",
        json={
            "prompt": "Explain RAG.",
            "candidate_a": "RAG can ground answers in retrieved evidence.",
            "candidate_b": "RAG always fixes hallucinations.",
            "model": "gpt-4o-mini",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "openai is not configured on this server."


def test_static_file_lookup_stays_inside_dist(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    assert main._safe_dist_file("index.html") == dist / "index.html"
    assert main._safe_dist_file("../secret.txt") is None


def test_debate_provider_keys_are_length_capped():
    data = {
        "topic": "Should AI govern cities?",
        "rounds": 1,
        "provider_keys": {"openai": "x" * 4097},
        "agents": [
            {
                "id": "nova",
                "name": "Nova",
                "role": "Optimist",
                "personality": "Sees constructive progress.",
                "provider": "openai",
                "model": "gpt-4o",
            },
            {
                "id": "rex",
                "name": "Rex",
                "role": "Skeptic",
                "personality": "Questions hidden risks.",
                "provider": "openai",
                "model": "gpt-4o",
            },
        ],
    }

    try:
        DebateRequest(**data)
    except ValueError as exc:
        assert "API key for openai is too long" in str(exc)
    else:
        raise AssertionError("Oversized provider keys should be rejected")
