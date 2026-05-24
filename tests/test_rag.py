import io
import json
import math

import pytest
from botocore.stub import Stubber

import rag
from schemas import QueryResponse


KB_ID = "TESTKBID00"
MODEL_ARN = "anthropic.claude-3-haiku-20240307-v1:0"


def _retrieve_response(items):
    return {
        "retrievalResults": [
            {
                "content": {"text": text},
                "score": score,
                "location": {
                    "type": "S3",
                    "s3Location": {"uri": uri},
                },
            }
            for (text, score, uri) in items
        ]
    }


def _claude_body(text):
    return {
        "body": io.BytesIO(
            json.dumps(
                {
                    "content": [{"type": "text", "text": text}],
                    "stop_reason": "end_turn",
                }
            ).encode()
        ),
        "contentType": "application/json",
    }


def test_retrieve_extracts_fields():
    items = [
        ("Refunds are available within 30 days.", 0.91, "s3://b/refund-policy.md"),
        ("Shipping ships in 3 days.", 0.42, "s3://b/sub/shipping-policy.md"),
    ]
    with Stubber(rag.agent_runtime) as stub:
        stub.add_response(
            "retrieve",
            _retrieve_response(items),
            expected_params={
                "knowledgeBaseId": KB_ID,
                "retrievalQuery": {"text": "refunds?"},
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {"numberOfResults": 5}
                },
            },
        )
        chunks = rag.retrieve("refunds?", 5, KB_ID)

    assert len(chunks) == 2
    assert chunks[0]["content"].startswith("Refunds are available")
    assert chunks[0]["score"] == pytest.approx(0.91)
    assert chunks[0]["s3_uri"] == "s3://b/refund-policy.md"
    assert chunks[0]["document"] == "refund-policy.md"
    assert chunks[1]["document"] == "shipping-policy.md"


def test_compute_confidence_single_score():
    # max=0.8, mean=0.8, stdev=0 -> 0.6*0.8 + 0.3*0.8 + 0.1*1 = 0.82
    c = rag.compute_confidence([0.8])
    assert c == pytest.approx(0.82)


def test_compute_confidence_multi_score():
    scores = [0.9, 0.7, 0.5]
    mean = sum(scores) / 3
    stdev = math.sqrt(sum((s - mean) ** 2 for s in scores) / (len(scores) - 1))
    expected = 0.6 * 0.9 + 0.3 * mean + 0.1 * (1 - stdev)
    expected = max(0.0, min(1.0, expected))
    assert rag.compute_confidence(scores) == pytest.approx(expected)


def test_compute_confidence_clamps_to_one():
    assert rag.compute_confidence([1.0, 1.0, 1.0]) == pytest.approx(1.0)


def test_compute_confidence_clamps_upper_bound():
    # raw would exceed 1.0 if not clamped; verify result stays <=1
    assert rag.compute_confidence([1.0, 1.0]) <= 1.0


def test_compute_confidence_empty_returns_zero():
    assert rag.compute_confidence([]) == 0.0


def test_run_query_end_to_end():
    items = [
        ("Refunds within 30 days.", 0.91, "s3://b/refund-policy.md"),
        ("Contact support for refund.", 0.65, "s3://b/refund-policy.md"),
    ]
    with Stubber(rag.agent_runtime) as agent_stub, Stubber(rag.runtime) as rt_stub:
        agent_stub.add_response("retrieve", _retrieve_response(items))
        rt_stub.add_response(
            "invoke_model",
            _claude_body("Refunds are available within 30 days [1]."),
        )
        result = rag.run_query(
            question="refund window?",
            top_k=5,
            kb_id=KB_ID,
            model_arn=MODEL_ARN,
            request_id="req-123",
            model_id_for_metadata="anthropic.claude-3-haiku-20240307-v1:0",
            latency_fn=lambda: 1240,
        )

    response = QueryResponse.model_validate(result)
    assert "Refunds" in response.answer
    assert 0.0 <= response.confidence <= 1.0
    assert response.confidence > 0.2
    assert len(response.sources) == 2
    assert response.sources[0].document == "refund-policy.md"
    assert response.metadata.request_id == "req-123"
    assert response.metadata.latency_ms == 1240
    assert response.metadata.retrieval_strategy == "bedrock-kb-s3vectors-titan-v2-topk"


def test_run_query_insufficient_context_override():
    items = [("Unrelated text.", 0.40, "s3://b/employee-handbook.md")]
    with Stubber(rag.agent_runtime) as agent_stub, Stubber(rag.runtime) as rt_stub:
        agent_stub.add_response("retrieve", _retrieve_response(items))
        rt_stub.add_response("invoke_model", _claude_body("INSUFFICIENT_CONTEXT"))
        result = rag.run_query(
            question="what is the capital of mars?",
            top_k=5,
            kb_id=KB_ID,
            model_arn=MODEL_ARN,
            request_id="req-x",
            model_id_for_metadata="anthropic.claude-3-haiku-20240307-v1:0",
            latency_fn=lambda: 100,
        )

    response = QueryResponse.model_validate(result)
    assert response.answer == rag.INSUFFICIENT_ANSWER
    assert response.confidence <= 0.2


def test_health_route_makes_no_aws_calls():
    import app

    # Stubbers with no responses queued will raise if any call is attempted.
    with Stubber(rag.agent_runtime) as agent_stub, Stubber(rag.runtime) as rt_stub:
        agent_stub.activate()
        rt_stub.activate()
        event = {
            "httpMethod": "GET",
            "resource": "/health",
            "path": "/health",
        }
        resp = app.handler(event, None)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"status": "ok"}
