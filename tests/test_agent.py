import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"


def _load_agent_module(name: str):
    spec = importlib.util.spec_from_file_location(
        f"agent_pkg_{name}", _AGENT_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"agent_pkg_{name}"] = module
    spec.loader.exec_module(module)
    return module


_schemas_mod = _load_agent_module("schemas")
sys.modules["_agent_schemas_alias"] = _schemas_mod
_original_schemas = sys.modules.get("schemas")
sys.modules["schemas"] = _schemas_mod
try:
    agent_rag = _load_agent_module("rag")
finally:
    if _original_schemas is not None:
        sys.modules["schemas"] = _original_schemas
    else:
        del sys.modules["schemas"]

QueryResponse = _schemas_mod.QueryResponse


MEMORY_ID = "TESTMEMORYID12"
ACTOR_ID = "user-1"
SESSION_ID = "sess-abc"
TABLE_NAME = "test-conversations"


def _retrieve_response(items):
    return {
        "retrievalResults": [
            {
                "content": {"text": text},
                "score": score,
                "location": {"type": "S3", "s3Location": {"uri": uri}},
            }
            for (text, score, uri) in items
        ]
    }


def _claude_body(text):
    return {
        "body": io.BytesIO(
            json.dumps(
                {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
            ).encode()
        ),
        "contentType": "application/json",
    }


def _empty_events():
    return {"events": []}


def _nonempty_events():
    from datetime import datetime, timezone
    return {
        "events": [
            {
                "memoryId": MEMORY_ID,
                "actorId": ACTOR_ID,
                "sessionId": SESSION_ID,
                "eventId": "evt-1",
                "eventTimestamp": datetime.now(timezone.utc),
                "payload": [
                    {"conversational": {"content": {"text": "prev"}, "role": "USER"}}
                ],
            }
        ]
    }


def test_is_first_turn_true_when_no_events():
    with Stubber(agent_rag.agentcore) as stub:
        stub.add_response(
            "list_events",
            _empty_events(),
            expected_params={
                "memoryId": MEMORY_ID,
                "sessionId": SESSION_ID,
                "actorId": ACTOR_ID,
                "maxResults": 1,
            },
        )
        assert agent_rag.is_first_turn(MEMORY_ID, ACTOR_ID, SESSION_ID) is True


def test_is_first_turn_false_when_events_exist():
    with Stubber(agent_rag.agentcore) as stub:
        stub.add_response("list_events", _nonempty_events())
        assert agent_rag.is_first_turn(MEMORY_ID, ACTOR_ID, SESSION_ID) is False


def test_write_memory_issues_single_create_event_with_two_items():
    captured = {}
    original = agent_rag.agentcore.create_event

    def fake_create_event(**kwargs):
        captured.update(kwargs)
        return {"event": {"eventId": "e1"}}

    agent_rag.agentcore.create_event = fake_create_event
    try:
        agent_rag.write_memory(MEMORY_ID, ACTOR_ID, SESSION_ID, "hi", "hello")
    finally:
        agent_rag.agentcore.create_event = original

    assert captured["memoryId"] == MEMORY_ID
    assert captured["actorId"] == ACTOR_ID
    assert captured["sessionId"] == SESSION_ID
    assert "eventTimestamp" in captured
    payload = captured["payload"]
    assert len(payload) == 2
    assert payload[0] == {"conversational": {"content": {"text": "hi"}, "role": "USER"}}
    assert payload[1] == {"conversational": {"content": {"text": "hello"}, "role": "ASSISTANT"}}


def test_generate_conversation_name_truncates_to_50_chars():
    long_title = "A" * 200
    with Stubber(agent_rag.runtime) as stub:
        stub.add_response("invoke_model", _claude_body(long_title))
        name = agent_rag.generate_conversation_name("any prompt", "any answer")
    assert len(name) <= 50
    assert name == "A" * 50


def test_generate_conversation_name_strips_quotes():
    with Stubber(agent_rag.runtime) as stub:
        stub.add_response("invoke_model", _claude_body('"Refund Policy Question"'))
        name = agent_rag.generate_conversation_name("refunds?", "Monthly plans have a 14 day window.")
    assert name == "Refund Policy Question"


def test_generate_conversation_name_uses_prompt_and_answer(monkeypatch):
    captured = {}

    def fake_invoke(prompt, model_arn, system, max_tokens):
        captured["prompt"] = prompt
        captured["model_arn"] = model_arn
        captured["system"] = system
        captured["max_tokens"] = max_tokens
        return "Refund Window"

    monkeypatch.setattr(agent_rag, "invoke_claude", fake_invoke)

    name = agent_rag.generate_conversation_name(
        "refunds?",
        "Monthly plans can be refunded within 14 days.",
    )

    assert name == "Refund Window"
    assert "User message: refunds?" in captured["prompt"]
    assert "Assistant answer: Monthly plans can be refunded within 14 days." in captured["prompt"]


def test_save_conversation_metadata_swallows_conditional_check_failed():
    table = agent_rag.dynamodb.Table(TABLE_NAME)
    original = table.put_item

    def fake_put(**kwargs):
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
            "PutItem",
        )

    table.put_item = fake_put
    agent_rag.dynamodb.Table = lambda _name: table
    try:
        agent_rag.save_conversation_metadata(TABLE_NAME, ACTOR_ID, SESSION_ID, "Title")
    finally:
        table.put_item = original


def test_save_conversation_metadata_reraises_other_errors():
    table = agent_rag.dynamodb.Table(TABLE_NAME)
    original = table.put_item

    def fake_put(**kwargs):
        raise ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "missing"}},
            "PutItem",
        )

    table.put_item = fake_put
    agent_rag.dynamodb.Table = lambda _name: table
    try:
        with pytest.raises(ClientError):
            agent_rag.save_conversation_metadata(TABLE_NAME, ACTOR_ID, SESSION_ID, "Title")
    finally:
        table.put_item = original


def test_run_query_first_turn_sets_conversation_name(monkeypatch):
    items = [
        ("Refunds within 30 days.", 0.91, "s3://b/refund-policy.md"),
        ("Contact support.", 0.65, "s3://b/refund-policy.md"),
    ]
    create_event_calls = []
    put_item_calls = []

    def fake_create_event(**kwargs):
        create_event_calls.append(kwargs)
        return {"event": {"eventId": "e1"}}

    def fake_table(_name):
        class _T:
            def put_item(self, **kwargs):
                put_item_calls.append(kwargs)
                return {}
        return _T()

    monkeypatch.setattr(agent_rag.agentcore, "create_event", fake_create_event)
    monkeypatch.setattr(agent_rag.dynamodb, "Table", fake_table)

    with Stubber(agent_rag.agentcore) as ac_stub, \
         Stubber(agent_rag.agent_runtime) as kb_stub, \
         Stubber(agent_rag.runtime) as rt_stub:
        ac_stub.add_response("list_events", _empty_events())
        kb_stub.add_response("retrieve", _retrieve_response(items))
        rt_stub.add_response("invoke_model", _claude_body("Refunds in 30 days [1]."))
        rt_stub.add_response("invoke_model", _claude_body("Refund Window"))

        result = agent_rag.run_query(
            prompt="refund window?",
            session_id=SESSION_ID,
            actor_id=ACTOR_ID,
            top_k=5,
            request_id="req-1",
        )

    response = QueryResponse.model_validate(result)
    assert response.conversation_name == "Refund Window"
    assert response.session_id == SESSION_ID
    assert len(response.sources) == 2
    assert "Refunds" in response.answer
    assert response.confidence > 0.2
    assert len(create_event_calls) == 1
    assert len(put_item_calls) == 1
    assert put_item_calls[0]["Item"]["conversation_name"] == "Refund Window"


def test_run_query_nth_turn_no_conversation_name(monkeypatch):
    items = [("Shipping ships in 3 days.", 0.80, "s3://b/shipping-policy.md")]
    create_event_calls = []

    def fake_create_event(**kwargs):
        create_event_calls.append(kwargs)
        return {"event": {"eventId": "e2"}}

    monkeypatch.setattr(agent_rag.agentcore, "create_event", fake_create_event)

    with Stubber(agent_rag.agentcore) as ac_stub, \
         Stubber(agent_rag.agent_runtime) as kb_stub, \
         Stubber(agent_rag.runtime) as rt_stub:
        ac_stub.add_response("list_events", _nonempty_events())
        kb_stub.add_response("retrieve", _retrieve_response(items))
        rt_stub.add_response("invoke_model", _claude_body("Ships in 3 days [1]."))

        result = agent_rag.run_query(
            prompt="when does it ship?",
            session_id=SESSION_ID,
            actor_id=ACTOR_ID,
            top_k=5,
            request_id="req-2",
        )

    response = QueryResponse.model_validate(result)
    assert response.conversation_name is None
    assert response.session_id == SESSION_ID
    assert len(response.sources) == 1
    assert len(create_event_calls) == 1


def test_run_query_insufficient_context_still_writes_memory(monkeypatch):
    create_event_calls = []

    def fake_create_event(**kwargs):
        create_event_calls.append(kwargs)
        return {"event": {"eventId": "e3"}}

    monkeypatch.setattr(agent_rag.agentcore, "create_event", fake_create_event)

    with Stubber(agent_rag.agentcore) as ac_stub, \
         Stubber(agent_rag.agent_runtime) as kb_stub, \
         Stubber(agent_rag.runtime) as rt_stub:
        ac_stub.add_response("list_events", _nonempty_events())
        kb_stub.add_response("retrieve", {"retrievalResults": []})

        result = agent_rag.run_query(
            prompt="what's the wifi password?",
            session_id=SESSION_ID,
            actor_id=ACTOR_ID,
            top_k=5,
            request_id="req-3",
        )

    response = QueryResponse.model_validate(result)
    assert response.answer == agent_rag.INSUFFICIENT_ANSWER
    assert response.confidence <= 0.2
    assert response.sources == []
    assert response.conversation_name is None
    assert len(create_event_calls) == 1
    sent_payload = create_event_calls[0]["payload"]
    assert sent_payload[1]["conversational"]["content"]["text"] == agent_rag.INSUFFICIENT_ANSWER
