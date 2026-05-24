import json
import os
import statistics
from typing import Dict, List

import boto3

RETRIEVAL_STRATEGY = "bedrock-kb-s3vectors-titan-v2-topk"
INSUFFICIENT = "INSUFFICIENT_CONTEXT"
INSUFFICIENT_ANSWER = "I don't have information about that in the knowledge base."

SYSTEM_PROMPT = (
    "Answer ONLY from provided context. "
    f"If insufficient, reply exactly `{INSUFFICIENT}`. "
    "Cite sources as [n]."
)

_REGION = os.environ.get("AWS_REGION", "us-east-1")
agent_runtime = boto3.client("bedrock-agent-runtime", region_name=_REGION)
runtime = boto3.client("bedrock-runtime", region_name=_REGION)


def retrieve(question: str, top_k: int, kb_id: str) -> List[Dict]:
    resp = agent_runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": question},
        retrievalConfiguration={
            "vectorSearchConfiguration": {"numberOfResults": top_k}
        },
    )
    chunks: List[Dict] = []
    for r in resp.get("retrievalResults", []):
        content = (r.get("content") or {}).get("text", "")
        score = float(r.get("score", 0.0))
        s3_uri = ((r.get("location") or {}).get("s3Location") or {}).get("uri", "")
        document = s3_uri.rsplit("/", 1)[-1] if s3_uri else ""
        chunks.append(
            {"content": content, "score": score, "s3_uri": s3_uri, "document": document}
        )
    return chunks


def build_prompt(question: str, chunks: List[Dict]) -> str:
    numbered = "\n\n".join(
        f"[{i + 1}] {c['content']}" for i, c in enumerate(chunks)
    )
    return f"Context:\n{numbered}\n\nQuestion: {question}"


def invoke_claude(prompt: str, model_arn: str) -> str:
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
    )
    resp = runtime.invoke_model(
        modelId=model_arn,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    payload = json.loads(resp["body"].read())
    parts = payload.get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def compute_confidence(scores: List[float]) -> float:
    if not scores:
        return 0.0
    max_s = max(scores)
    mean_s = statistics.mean(scores)
    stdev_s = statistics.stdev(scores) if len(scores) > 1 else 0.0
    raw = 0.6 * max_s + 0.3 * mean_s + 0.1 * (1.0 - stdev_s)
    return max(0.0, min(1.0, raw))


def _snippet(text: str, limit: int = 240) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def run_query(
    question: str,
    top_k: int,
    kb_id: str,
    model_arn: str,
    request_id: str,
    model_id_for_metadata: str,
    latency_fn,
) -> Dict:
    chunks = retrieve(question, top_k, kb_id)
    prompt = build_prompt(question, chunks)
    answer = invoke_claude(prompt, model_arn) if chunks else INSUFFICIENT
    scores = [c["score"] for c in chunks]
    confidence = compute_confidence(scores)

    if answer.strip() == INSUFFICIENT or not chunks:
        answer = INSUFFICIENT_ANSWER
        confidence = min(confidence, 0.2)

    sources = [
        {
            "s3_uri": c["s3_uri"],
            "document": c["document"],
            "score": c["score"],
            "snippet": _snippet(c["content"]),
        }
        for c in chunks
    ]

    return {
        "answer": answer,
        "confidence": confidence,
        "sources": sources,
        "metadata": {
            "model": model_id_for_metadata,
            "retrieval_strategy": RETRIEVAL_STRATEGY,
            "request_id": request_id,
            "latency_ms": latency_fn(),
        },
    }
