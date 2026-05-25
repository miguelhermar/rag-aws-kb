import json
import os
import statistics
import time
from datetime import datetime, timezone
from typing import Dict, List

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

RETRIEVAL_STRATEGY = "bedrock-kb-s3vectors-titan-v2-topk"
INSUFFICIENT = "INSUFFICIENT_CONTEXT"
INSUFFICIENT_ANSWER = "I don't have information about that in the knowledge base."
MAX_TITLE_CHARS = 50

SYSTEM_PROMPT = (
    "Answer ONLY from the provided context. "
    f"If the context is insufficient to answer the question, your entire response MUST be the single token `{INSUFFICIENT}` "
    "with no other characters, words, or explanation. "
    "When you can answer, cite sources inline as [n]."
)

TITLE_SYSTEM_PROMPT = (
    "You generate short conversation titles. "
    "Reply with only the title text — no quotes, no punctuation at the end, no preamble."
)

_REGION = os.environ.get("AWS_REGION", "us-east-1")

KB_ID = os.environ["KB_ID"]
MODEL_ARN = os.environ["MODEL_ARN"]
MODEL_ID = os.environ["MODEL_ID"]
MEMORY_ID = os.environ["MEMORY_ID"]
CONVERSATIONS_TABLE = os.environ["CONVERSATIONS_TABLE"]

agent_runtime = boto3.client("bedrock-agent-runtime", region_name=_REGION)
runtime = boto3.client("bedrock-runtime", region_name=_REGION)
agentcore = boto3.client("bedrock-agentcore", region_name=_REGION)
dynamodb = boto3.resource("dynamodb", region_name=_REGION)


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


def invoke_claude(prompt: str, model_arn: str, system: str = SYSTEM_PROMPT, max_tokens: int = 1024) -> str:
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system,
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


def is_first_turn(memory_id: str, actor_id: str, session_id: str) -> bool:
    resp = agentcore.list_events(
        memoryId=memory_id,
        sessionId=session_id,
        actorId=actor_id,
        maxResults=1,
    )
    return not resp.get("events", [])


def write_memory(memory_id: str, actor_id: str, session_id: str, prompt: str, answer: str) -> None:
    agentcore.create_event(
        memoryId=memory_id,
        actorId=actor_id,
        sessionId=session_id,
        eventTimestamp=datetime.now(timezone.utc),
        payload=[
            {"conversational": {"content": {"text": prompt}, "role": "USER"}},
            {"conversational": {"content": {"text": answer}, "role": "ASSISTANT"}},
        ],
    )


def generate_conversation_name(prompt: str) -> str:
    instruction = (
        "Generate a 3-5 word title (max 50 chars) for this user message. "
        "Reply with only the title, no quotes. "
        f"Message: {prompt}"
    )
    title = invoke_claude(instruction, MODEL_ARN, system=TITLE_SYSTEM_PROMPT, max_tokens=32).strip()
    title = title.strip('"\'').strip()
    return title[:MAX_TITLE_CHARS]


def save_conversation_metadata(table_name: str, actor_id: str, session_id: str, name: str) -> None:
    table = dynamodb.Table(table_name)
    try:
        table.put_item(
            Item={
                "actor_id": actor_id,
                "session_id": session_id,
                "conversation_name": name,
                "created_at": int(time.time()),
            },
            ConditionExpression=Attr("session_id").not_exists(),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def run_query(
    prompt: str,
    session_id: str,
    actor_id: str,
    top_k: int,
    request_id: str,
) -> Dict:
    start = time.perf_counter()

    first_turn = is_first_turn(MEMORY_ID, actor_id, session_id)

    chunks = retrieve(prompt, top_k, KB_ID)
    rag_prompt = build_prompt(prompt, chunks)
    answer = invoke_claude(rag_prompt, MODEL_ARN) if chunks else INSUFFICIENT
    scores = [c["score"] for c in chunks]
    confidence = compute_confidence(scores)

    if answer.strip().startswith(INSUFFICIENT) or not chunks:
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

    write_memory(MEMORY_ID, actor_id, session_id, prompt, answer)

    conversation_name = None
    if first_turn:
        conversation_name = generate_conversation_name(prompt)
        save_conversation_metadata(CONVERSATIONS_TABLE, actor_id, session_id, conversation_name)

    latency_ms = int((time.perf_counter() - start) * 1000)

    return {
        "answer": answer,
        "confidence": confidence,
        "sources": sources,
        "metadata": {
            "model": MODEL_ID,
            "retrieval_strategy": RETRIEVAL_STRATEGY,
            "request_id": request_id,
            "latency_ms": latency_ms,
        },
        "session_id": session_id,
        "conversation_name": conversation_name,
    }
