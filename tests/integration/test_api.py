from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.base import LLMError
from app.llm.fake import FakeProvider, Script
from app.main import create_app
from tests.conftest import TOKEN, chat, parse_sse

# --- health & auth -------------------------------------------------------------------------


def test_health_public_is_minimal(client: TestClient) -> None:
    resp = client.get("/health", headers={"Authorization": ""})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_authenticated_lists_integrations_without_secrets(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["integrations"]["voice.stt"] is False  # tests run without speech models
    assert body["voice"]["wake_word"] == "Alfred"
    assert body["llm"]["provider"] == "fake"
    assert "SECRET" not in str(body)


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Basic " + TOKEN, TOKEN])
def test_v1_requires_bearer_token(client: TestClient, header: str | None) -> None:
    headers = {"Authorization": header} if header else {"Authorization": ""}
    assert client.get("/v1/conversations", headers=headers).status_code == 401
    assert client.post("/v1/chat", json={"message": "hi"}, headers=headers).status_code == 401


def test_ip_allowlist(settings: Settings, fake_llm: FakeProvider) -> None:
    settings.ip_allowlist = ["10.0.0.0/8"]
    with TestClient(create_app(settings, llm=fake_llm)) as c:  # TestClient host is "testclient"
        resp = c.get("/v1/conversations", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 403


def test_startup_refuses_weak_token(settings: Settings) -> None:
    settings.alfred_api_token = "short"  # type: ignore[assignment]  # coerced to SecretStr
    with pytest.raises(RuntimeError, match="ALFRED_API_TOKEN"), TestClient(create_app(settings)):
        pass


def test_starts_without_any_llm_and_explains(settings: Settings) -> None:
    settings.llm_provider = "anthropic"  # no key configured
    with TestClient(create_app(settings)) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        assert c.get("/health").json()["llm"]["configured"] is False
        events = chat(c, "Hello")
        err = next(e for e in events if e["type"] == "error")
        assert err["data"]["code"] == "llm_unavailable"
        assert events[-2]["type"] == "done" and events[-2]["data"]["status"] == "error"


def test_cors_only_allows_configured_origins(client: TestClient) -> None:
    ok = client.options(
        "/v1/chat",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.options(
        "/v1/chat",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers


# --- chat ----------------------------------------------------------------------------------


def test_chat_streams_typed_events_and_persists(client: TestClient, fake_llm: FakeProvider) -> None:
    fake_llm.scripts = [Script(text="Good evening, sir. All is in order.")]
    events = chat(client, "Good evening, Alfred")

    types = [e["type"] for e in events]
    assert types[0] == "state" and events[0]["data"]["value"] == "thinking"
    assert types[-1] == "state" and events[-1]["data"]["value"] == "idle"
    assert types[-2] == "done"
    assert "".join(e["data"]["text"] for e in events if e["type"] == "token") == (
        "Good evening, sir. All is in order."
    )
    conv_id = events[0]["conversation_id"]
    assert all(e["conversation_id"] == conv_id for e in events)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    done = events[-2]["data"]
    assert done["status"] == "complete" and done["usage"]["output_tokens"] > 0
    assert done["provider"] == "fake" and done["message_id"]

    detail = client.get(f"/v1/conversations/{conv_id}").json()
    assert detail["title"] == "Good evening, Alfred"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    full = client.get(f"/v1/conversations/{conv_id}?include_context=true").json()
    assert [m["role"] for m in full["messages"]] == ["user", "context", "assistant"]


def test_chat_continues_conversation_with_history(
    client: TestClient, fake_llm: FakeProvider
) -> None:
    first = chat(client, "My tea is Earl Grey.")
    conv_id = first[0]["conversation_id"]
    chat(client, "Which tea?", conversation_id=conv_id, output_mode="voice")
    request = fake_llm.requests[-1]
    assert [m.role for m in request.messages] == ["user", "context", "assistant", "user", "context"]
    assert request.messages[0].text == "My tea is Earl Grey."
    assert "spoken aloud" in request.messages[-1].text
    # the clock lives in the per-turn note, never in the (cached) system prompt
    assert "given in the latest turn context note" in request.system
    assert "2026" not in request.system and "Current date and time:" in request.messages[-1].text


def test_chat_unknown_conversation_404(client: TestClient) -> None:
    resp = client.post("/v1/chat", json={"message": "hi", "conversation_id": "nope"})
    assert resp.status_code == 404


def test_chat_validation(client: TestClient) -> None:
    assert client.post("/v1/chat", json={"message": ""}).status_code == 422
    assert client.post("/v1/chat", json={"message": "x", "output_mode": "smoke"}).status_code == 422
    assert client.post("/v1/chat", json={"message": "x", "attachments": [{}]}).status_code == 422


def test_chat_llm_error_becomes_safe_error_event(
    client: TestClient, fake_llm: FakeProvider
) -> None:
    fake_llm.scripts = [Script(error=LLMError("upstream said sk-ant-secret", code="llm_upstream"))]
    events = chat(client, "Hello")
    err = next(e for e in events if e["type"] == "error")
    assert err["data"]["code"] == "llm_upstream"
    assert "sk-ant" not in err["data"]["message"]
    assert events[-2]["data"]["status"] == "error"


def test_turn_timeout(client: TestClient, fake_llm: FakeProvider, settings: Settings) -> None:
    settings.turn_timeout_s = 0.05
    fake_llm.scripts = [Script(text="late", delay_s=1.0)]
    events = chat(client, "Hello")
    assert any(e["type"] == "error" and e["data"]["code"] == "turn_timeout" for e in events)


# --- conversations -------------------------------------------------------------------------


def test_list_paginate_and_delete(client: TestClient) -> None:
    ids = [chat(client, f"message {i}")[0]["conversation_id"] for i in range(3)]
    page1 = client.get("/v1/conversations?limit=2").json()
    assert page1["total"] == 3 and len(page1["items"]) == 2
    assert page1["items"][0]["id"] == ids[-1]  # newest first
    page2 = client.get(
        "/v1/conversations", params={"limit": 2, "before": page1["next_before"]}
    ).json()
    assert [c["id"] for c in page2["items"]] == [ids[0]] and page2["next_before"] is None

    assert client.delete(f"/v1/conversations/{ids[0]}").status_code == 204
    assert client.get(f"/v1/conversations/{ids[0]}").status_code == 404
    assert client.delete(f"/v1/conversations/{ids[0]}").status_code == 404


def test_cost_totals_accumulate(client: TestClient, settings: Settings) -> None:
    events = chat(client, "one")
    conv_id = events[0]["conversation_id"]
    second = chat(client, "two", conversation_id=conv_id)
    totals = second[-2]["data"]["conversation_totals"]
    detail = client.get(f"/v1/conversations/{conv_id}").json()
    assert totals["input_tokens"] == detail["input_tokens"] > 0


def test_openapi_documents_v1_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in ["/health", "/v1/chat", "/v1/conversations", "/v1/conversations/{conversation_id}"]:
        assert path in paths


def test_sse_parser_ignores_pings() -> None:
    assert parse_sse(': ping\n\nevent: token\ndata: {"a": 1}\n\n') == [{"a": 1}]
