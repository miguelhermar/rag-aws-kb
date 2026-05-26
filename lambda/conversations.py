"""Handlers for /conversations and /conversations/{session_id}.

`list_conversations`: DDB Query by `actor_id` (from JWT `sub` claim).
`get_conversation`:    AgentCore Memory list_events (native conversational payload).

The agent writes msgpack-free conversational events: one `create_event` per turn
with two payload items (USER then ASSISTANT). This module parses only that shape.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

# AgentCore Memory's blob Document field round-trips as a Java toString()
# style repr on ListEvents — see agent/rag.py write_memory for the write-side
# workaround. On read, the structure looks like `{b64=<urlsafe-base64>}` and
# we extract the encoded payload via this regex (urlsafe-base64 alphabet:
# alnum, `-`, `_`, `=`).
_B64_RE = re.compile(r"b64=([A-Za-z0-9_\-=]+)")


def _extract_blob_meta(blob_value) -> Optional[Dict]:
    """Best-effort decode of an AgentCore Memory blob payload item.

    Returns the deserialized dict (without the `kind` key) when the blob looks
    like our assistant-metadata sidecar, or None on any parse failure.
    """
    if blob_value is None:
        return None
    if isinstance(blob_value, dict):
        b64 = blob_value.get("b64")
    else:
        m = _B64_RE.search(str(blob_value))
        b64 = m.group(1) if m else None
    if not b64:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(decoded, dict) or decoded.get("kind") != "assistant_metadata":
        return None
    return {k: v for k, v in decoded.items() if k != "kind"}

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_agentcore = boto3.client("bedrock-agentcore", region_name=_REGION)


def list_conversations(actor_id: str, table_name: str) -> List[Dict]:
    table = _ddb.Table(table_name)
    resp = table.query(KeyConditionExpression=Key("actor_id").eq(actor_id))
    items = resp.get("Items", [])
    while "LastEvaluatedKey" in resp:
        resp = table.query(
            KeyConditionExpression=Key("actor_id").eq(actor_id),
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items", []))

    return [
        {
            "session_id": i["session_id"],
            "conversation_name": i.get("conversation_name", ""),
            # created_at comes back as Decimal from DDB; coerce to int for JSON.
            "created_at": int(i["created_at"]) if "created_at" in i else 0,
        }
        for i in items
    ]


def get_conversation(
    actor_id: str, session_id: str, memory_id: str
) -> List[Dict]:
    """Read a conversation from AgentCore Memory.

    Each event represents one turn. We expect three payload items per turn:
      1. conversational role=USER
      2. conversational role=ASSISTANT
      3. blob with kind="assistant_metadata" carrying {sources, confidence,
         latency_ms, model_id, retrieval_strategy}  (added in Phase 13)

    Events written before Phase 13 don't have the blob — those assistant
    messages return without a `payload` field, and the client falls back to
    plain-text rendering.
    """
    messages: List[Dict] = []
    next_token: str | None = None

    while True:
        kwargs = {
            "memoryId": memory_id,
            "sessionId": session_id,
            "actorId": actor_id,
            "includePayloads": True,
            "maxResults": 100,
        }
        if next_token:
            kwargs["nextToken"] = next_token

        resp = _agentcore.list_events(**kwargs)

        for event in resp.get("events", []):
            ts = event.get("eventTimestamp")
            ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

            turn_messages: List[Dict] = []
            assistant_meta: Dict | None = None
            for item in event.get("payload", []):
                conv = item.get("conversational")
                if conv:
                    text = (conv.get("content") or {}).get("text", "")
                    role = (conv.get("role") or "").lower()
                    turn_messages.append(
                        {"role": role, "content": text, "timestamp": ts_iso}
                    )
                    continue
                if "blob" in item:
                    parsed = _extract_blob_meta(item.get("blob"))
                    if parsed is not None:
                        assistant_meta = parsed

            if assistant_meta is not None:
                for m in turn_messages:
                    if m["role"] == "assistant":
                        m["payload"] = {
                            "answer": m["content"],
                            "sources": assistant_meta.get("sources", []),
                            "confidence": assistant_meta.get("confidence", 0.0),
                            "conversation_name": None,
                            "metadata": {
                                "model": assistant_meta.get("model_id", ""),
                                "retrieval_strategy": assistant_meta.get("retrieval_strategy", ""),
                                "request_id": "",
                                "latency_ms": int(assistant_meta.get("latency_ms", 0) or 0),
                            },
                        }
                        break

            messages.extend(turn_messages)

        next_token = resp.get("nextToken")
        if not next_token:
            break

    messages.sort(key=lambda m: m["timestamp"])
    return messages
