import pytest
from pydantic import ValidationError

from schemas import ErrorEnvelope, QueryRequest, QueryResponse


def test_query_request_roundtrip_defaults():
    req = QueryRequest.model_validate({"question": "What is the refund policy?"})
    assert req.top_k == 5
    assert req.model_dump() == {
        "question": "What is the refund policy?",
        "top_k": 5,
        "session_id": None,
    }


def test_query_request_custom_top_k():
    req = QueryRequest.model_validate({"question": "hi", "top_k": 3})
    assert req.top_k == 3


def test_query_request_rejects_negative_top_k():
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"question": "hi", "top_k": -1})


def test_query_request_rejects_zero_top_k():
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"question": "hi", "top_k": 0})


def test_query_request_rejects_missing_question():
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"top_k": 5})


def test_query_request_rejects_empty_question():
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"question": "", "top_k": 5})


def test_query_request_accepts_session_id():
    sid = "abcdefgh-1234-5678-9012-abcdefghijkl"
    req = QueryRequest.model_validate({"question": "hi", "session_id": sid})
    assert req.session_id == sid


def test_query_request_session_id_defaults_none():
    req = QueryRequest.model_validate({"question": "hi"})
    assert req.session_id is None


def test_query_response_roundtrip():
    payload = {
        "answer": "Refunds within 30 days.",
        "confidence": 0.84,
        "sources": [
            {
                "s3_uri": "s3://bucket/refund-policy.md",
                "document": "refund-policy.md",
                "score": 0.91,
                "snippet": "Refunds are available within 30 days...",
            }
        ],
        "metadata": {
            "model": "anthropic.claude-haiku-4-5-20251001-v1:0",
            "retrieval_strategy": "bedrock-kb-s3vectors-titan-v2-topk",
            "request_id": "11111111-1111-1111-1111-111111111111",
            "latency_ms": 1240,
        },
        "session_id": "sess-abcdefgh-1234-5678-9012-abcdefghijkl",
        "conversation_name": "Refund policy questions",
    }
    resp = QueryResponse.model_validate(payload)
    assert resp.model_dump() == payload


def test_query_response_conversation_name_optional():
    payload = {
        "answer": "x",
        "confidence": 0.5,
        "sources": [],
        "metadata": {
            "model": "m",
            "retrieval_strategy": "s",
            "request_id": "r",
            "latency_ms": 1,
        },
        "session_id": "sess-abcdefgh-1234-5678-9012-abcdefghijkl",
    }
    resp = QueryResponse.model_validate(payload)
    assert resp.conversation_name is None


def test_query_response_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        QueryResponse.model_validate(
            {
                "answer": "x",
                "confidence": 1.5,
                "sources": [],
                "metadata": {
                    "model": "m",
                    "retrieval_strategy": "s",
                    "request_id": "r",
                    "latency_ms": 1,
                },
                "session_id": "sess-x",
            }
        )


def test_query_response_rejects_confidence_below_zero():
    with pytest.raises(ValidationError):
        QueryResponse.model_validate(
            {
                "answer": "x",
                "confidence": -0.1,
                "sources": [],
                "metadata": {
                    "model": "m",
                    "retrieval_strategy": "s",
                    "request_id": "r",
                    "latency_ms": 1,
                },
                "session_id": "sess-x",
            }
        )


def test_query_response_rejects_missing_metadata():
    with pytest.raises(ValidationError):
        QueryResponse.model_validate(
            {"answer": "x", "confidence": 0.5, "sources": [], "session_id": "s"}
        )


def test_query_response_rejects_missing_session_id():
    with pytest.raises(ValidationError):
        QueryResponse.model_validate(
            {
                "answer": "x",
                "confidence": 0.5,
                "sources": [],
                "metadata": {
                    "model": "m",
                    "retrieval_strategy": "s",
                    "request_id": "r",
                    "latency_ms": 1,
                },
            }
        )


@pytest.mark.parametrize("kind", ["InvalidRequest", "Unauthorized", "Internal"])
def test_error_envelope_each_enum(kind):
    env = ErrorEnvelope(error=kind, message="msg", request_id="rid")
    assert env.model_dump() == {"error": kind, "message": "msg", "request_id": "rid"}


def test_error_envelope_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        ErrorEnvelope.model_validate(
            {"error": "Banana", "message": "msg", "request_id": "rid"}
        )
