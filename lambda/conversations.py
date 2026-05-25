"""Handlers for /conversations and /conversations/{session_id}.

`list_conversations`: DDB Query by `actor_id` (from JWT `sub` claim).
`get_conversation`:    AgentCore Memory list_events (native conversational payload).

The agent writes msgpack-free conversational events: one `create_event` per turn
with two payload items (USER then ASSISTANT). This module parses only that shape.
"""

from __future__ import annotations

import os
from typing import Dict, List

import boto3
from boto3.dynamodb.conditions import Key

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
            for item in event.get("payload", []):
                conv = item.get("conversational")
                if not conv:
                    continue
                text = (conv.get("content") or {}).get("text", "")
                role = conv.get("role", "")
                messages.append(
                    {"role": role, "content": text, "timestamp": ts_iso}
                )

        next_token = resp.get("nextToken")
        if not next_token:
            break

    messages.sort(key=lambda m: m["timestamp"])
    return messages
