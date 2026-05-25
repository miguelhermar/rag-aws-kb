#!/usr/bin/env python3
"""Smoke test for the deployed RAG-AWS API (Cognito JWT auth).

Exits 0 iff:
  1. GET  /health                                 -> 200 {"status":"ok"}
  2. POST /query (valid Bearer token)             -> 200, schema-valid QueryResponse,
                                                     non-empty sources, confidence in [0,1]
  3. POST /query (no Authorization header)        -> 401 or 403
  4. POST /query (empty question, valid token)    -> 400 error="InvalidRequest"
  5. GET  /conversations (valid Bearer token)     -> 200 + JSON array
  6. POST /query-stream (Function URL, SSE)       -> 200 text/event-stream, accumulated
                                                     answer parses as QueryResponse
  7. Upload + ingestion flow (90s)                 -> mint URL, PUT bytes, start ingestion,
                                                     poll to COMPLETE, then /query verifies retrieval
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
CDK_OUTPUTS = REPO_ROOT / "cdk-outputs.json"

SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from get_id_token import get_id_token, _fetch_password  # noqa: E402

LAMBDA_DIR = REPO_ROOT / "lambda"
if str(LAMBDA_DIR) not in sys.path:
    sys.path.insert(0, str(LAMBDA_DIR))
from schemas import QueryRequest, QueryResponse  # noqa: E402


def _load_outputs() -> dict:
    if not CDK_OUTPUTS.exists():
        return {}
    try:
        return json.loads(CDK_OUTPUTS.read_text())
    except json.JSONDecodeError:
        return {}


def _api_outputs() -> dict:
    outs = _load_outputs()
    merged = {}
    for stack in ("ApiStack", "AuthStack"):
        merged.update(outs.get(stack, {}))
    return merged


def _fetch_id_token(args) -> str:
    password = _fetch_password(args.password_secret_arn, args.region)
    client_secret = (
        _fetch_password(args.client_secret_arn, args.region)
        if getattr(args, "client_secret_arn", None)
        else None
    )
    return get_id_token(
        args.user_pool_id, args.client_id, args.username, password, args.region,
        client_secret,
    )


class CheckRunner:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        tag = "[OK]  " if ok else "[FAIL]"
        line = f"{tag} {name}"
        if detail:
            line += f" — {detail}"
        print(line)
        if not ok:
            self.failures.append(name)


def check_health(runner: CheckRunner, base: str) -> None:
    try:
        r = requests.get(f"{base}/health", timeout=15)
    except requests.RequestException as e:
        runner.record("GET /health -> 200", False, f"network error: {e}")
        return
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    ok = r.status_code == 200 and body == {"status": "ok"}
    runner.record("GET /health -> 200 {status:ok}", ok, f"status={r.status_code} body={body}")


def check_query_happy(runner: CheckRunner, base: str, token: str) -> None:
    payload = {
        "question": "What is the refund window for monthly plans?",
        "session_id": "s-" + uuid.uuid4().hex,
        "top_k": 3,
    }
    QueryRequest.model_validate(payload)
    try:
        r = requests.post(
            f"{base}/query",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        runner.record("POST /query (valid)", False, f"network error: {e}")
        return
    if r.status_code != 200:
        runner.record("POST /query (valid) -> 200", False, f"status={r.status_code} body={r.text[:200]}")
        return
    try:
        parsed = QueryResponse.model_validate(r.json())
    except Exception as e:
        runner.record("POST /query (valid) -> schema-valid", False, f"validation error: {e}")
        return
    ok_sources = len(parsed.sources) > 0
    ok_conf = 0.0 <= parsed.confidence <= 1.0
    runner.record(
        "POST /query (valid) -> 200 + schema + sources + conf",
        ok_sources and ok_conf,
        f"sources={len(parsed.sources)} confidence={parsed.confidence:.3f}",
    )


def check_query_no_token(runner: CheckRunner, base: str) -> None:
    try:
        r = requests.post(
            f"{base}/query",
            headers={"Content-Type": "application/json"},
            json={"question": "anything", "session_id": "s-" + uuid.uuid4().hex, "top_k": 3},
            timeout=15,
        )
    except requests.RequestException as e:
        runner.record("POST /query (no token) -> 401/403", False, f"network error: {e}")
        return
    runner.record(
        "POST /query (no token) -> 401/403",
        r.status_code in (401, 403),
        f"status={r.status_code}",
    )


def check_query_empty(runner: CheckRunner, base: str, token: str) -> None:
    try:
        r = requests.post(
            f"{base}/query",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"question": "", "session_id": "s-" + uuid.uuid4().hex},
            timeout=15,
        )
    except requests.RequestException as e:
        runner.record("POST /query (empty) -> 400", False, f"network error: {e}")
        return
    body = {}
    try:
        body = r.json()
    except ValueError:
        pass
    ok = r.status_code == 400 and body.get("error") == "InvalidRequest"
    runner.record(
        "POST /query (empty) -> 400 InvalidRequest",
        ok,
        f"status={r.status_code} error={body.get('error')}",
    )


# Phase 9a — SSE smoke check
def _iter_sse(resp):
    event_name = "message"
    data_lines: list[str] = []
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.rstrip("\r")
        if line == "":
            if data_lines:
                blob = "\n".join(data_lines)
                try:
                    yield event_name, json.loads(blob)
                except ValueError:
                    yield event_name, {"raw": blob}
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())


def check_query_stream(runner: CheckRunner, stream_url: str | None, token: str) -> None:
    if not stream_url:
        runner.record(
            "POST /query-stream (SSE)",
            False,
            "no --stream-url and no StreamFunctionUrl in cdk-outputs.json",
        )
        return
    url = stream_url.rstrip("/") + "/query-stream"
    payload = {
        "question": "What is the refund window for monthly plans?",
        "session_id": "s-" + uuid.uuid4().hex,
        "top_k": 3,
    }
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json=payload,
            timeout=60,
            stream=True,
        )
    except requests.RequestException as e:
        runner.record("POST /query-stream (SSE)", False, f"network error: {e}")
        return
    if r.status_code != 200:
        runner.record(
            "POST /query-stream (SSE) -> 200",
            False,
            f"status={r.status_code} body={r.text[:200]}",
        )
        return
    ctype = r.headers.get("content-type", "")
    if "text/event-stream" not in ctype:
        runner.record(
            "POST /query-stream (SSE) -> text/event-stream",
            False,
            f"content-type={ctype}",
        )
        return

    accumulated = ""
    sources: list = []
    done_evt: dict | None = None
    err_evt: dict | None = None
    saw_meta = False
    for event_name, data in _iter_sse(r):
        if event_name == "meta":
            saw_meta = True
        elif event_name == "token":
            accumulated += data.get("text", "")
        elif event_name == "sources":
            sources = data.get("sources") or []
        elif event_name == "done":
            done_evt = data
            break
        elif event_name == "error":
            err_evt = data
            break

    if err_evt:
        runner.record("POST /query-stream (SSE)", False, f"error event: {err_evt}")
        return
    if not (saw_meta and done_evt):
        runner.record(
            "POST /query-stream (SSE) framing",
            False,
            f"saw_meta={saw_meta} done={done_evt is not None}",
        )
        return

    # Assemble a QueryResponse-shaped dict and pydantic-validate it (same
    # consistency check as the non-streaming /query smoke).
    assembled = {
        "answer": accumulated.strip(),
        "confidence": float(done_evt.get("confidence", 0.0)),
        "sources": sources,
        "metadata": {
            "model": done_evt.get("model_id", ""),
            "retrieval_strategy": "bedrock-kb-s3vectors-titan-v2-topk-stream",
            "request_id": "smoke",
            "latency_ms": int(done_evt.get("latency_ms", 0)),
        },
        "session_id": payload["session_id"],
        "conversation_name": done_evt.get("conversation_name"),
    }
    try:
        parsed = QueryResponse.model_validate(assembled)
    except Exception as e:
        runner.record(
            "POST /query-stream (SSE) -> schema-valid", False, f"validation error: {e}"
        )
        return
    runner.record(
        "POST /query-stream (SSE) -> 200 + tokens + done",
        len(parsed.sources) > 0 and 0.0 <= parsed.confidence <= 1.0,
        f"sources={len(parsed.sources)} confidence={parsed.confidence:.3f}",
    )


# Phase 9b — upload + ingestion smoke
def check_upload_ingest_flow(runner: CheckRunner, base: str, token: str) -> None:
    name = "Upload + ingestion flow (90s)"
    marker = f"goblin-token-{uuid.uuid4().hex[:8]}"
    body = (
        f"# Phase 9b smoke marker\n\n"
        f"The secret refund window phrase is: {marker}.\n"
        f"This file was uploaded by the smoke test to verify ingestion.\n"
    ).encode("utf-8")
    filename = f"smoke-{uuid.uuid4().hex[:8]}.md"
    content_type = "text/markdown"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 1) Mint presigned URL.
    try:
        r = requests.post(
            f"{base}/documents",
            headers=headers,
            json={"filename": filename, "content_type": content_type},
            timeout=15,
        )
    except requests.RequestException as e:
        runner.record(name, False, f"network error on POST /documents: {e}")
        return
    if r.status_code != 200:
        runner.record(name, False, f"POST /documents -> {r.status_code} {r.text[:160]}")
        return
    mint = r.json()
    upload_url = mint["upload_url"]
    key = mint["key"]

    # 2) PUT bytes directly to S3 with the SAME Content-Type signed.
    try:
        put = requests.put(
            upload_url,
            data=body,
            headers={"Content-Type": content_type},
            timeout=30,
        )
    except requests.RequestException as e:
        runner.record(name, False, f"network error on S3 PUT: {e}")
        return
    if put.status_code not in (200, 204):
        runner.record(name, False, f"S3 PUT -> {put.status_code} {put.text[:160]}")
        return

    # 3) Start ingestion.
    try:
        r = requests.post(
            f"{base}/ingest",
            headers=headers,
            json={"key": key},
            timeout=15,
        )
    except requests.RequestException as e:
        runner.record(name, False, f"network error on POST /ingest: {e}")
        return
    if r.status_code != 200:
        runner.record(name, False, f"POST /ingest -> {r.status_code} {r.text[:160]}")
        return
    job_id = r.json().get("job_id")
    if not job_id:
        runner.record(name, False, "POST /ingest returned no job_id")
        return

    # 4) Poll every 2s up to 90s.
    deadline = time.monotonic() + 90.0
    final_status = None
    final_body: dict = {}
    while time.monotonic() < deadline:
        try:
            r = requests.get(
                f"{base}/ingest/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
        except requests.RequestException as e:
            runner.record(name, False, f"network error on GET /ingest/{job_id}: {e}")
            return
        if r.status_code != 200:
            runner.record(name, False, f"GET /ingest/{job_id} -> {r.status_code}")
            return
        final_body = r.json()
        final_status = final_body.get("status")
        if final_status in ("COMPLETE", "FAILED", "STOPPED"):
            break
        time.sleep(2.0)

    if final_status != "COMPLETE":
        runner.record(
            name,
            False,
            f"ingestion did not reach COMPLETE; last status={final_status} body={final_body}",
        )
        return

    # 5) Query for the unique marker phrase to verify retrieval.
    try:
        r = requests.post(
            f"{base}/query",
            headers=headers,
            json={
                "question": f"What is the secret phrase '{marker}'?",
                "session_id": "s-" + uuid.uuid4().hex,
                "top_k": 3,
            },
            timeout=30,
        )
    except requests.RequestException as e:
        runner.record(name, False, f"network error on follow-up /query: {e}")
        return
    if r.status_code != 200:
        runner.record(name, False, f"follow-up /query -> {r.status_code} {r.text[:160]}")
        return
    parsed = r.json()
    sources = parsed.get("sources") or []
    # Retrieval should surface the just-uploaded doc as a source.
    matched = any(key in (s.get("s3_uri") or "") for s in sources)
    runner.record(
        name,
        matched,
        f"key={key} sources={len(sources)} matched={matched} status={final_status}",
    )


def check_conversations_list(runner: CheckRunner, base: str, token: str) -> None:
    try:
        r = requests.get(
            f"{base}/conversations",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except requests.RequestException as e:
        runner.record("GET /conversations -> 200 array", False, f"network error: {e}")
        return
    body = None
    try:
        body = r.json()
    except ValueError:
        pass
    items = body if isinstance(body, list) else (body or {}).get("conversations")
    ok = r.status_code == 200 and isinstance(items, list)
    runner.record(
        "GET /conversations -> 200 array",
        ok,
        f"status={r.status_code} items={len(items) if isinstance(items, list) else 'n/a'}",
    )


def main() -> int:
    outs = _api_outputs()
    p = argparse.ArgumentParser(description="Smoke-test the deployed RAG-AWS API (Cognito JWT).")
    p.add_argument("--api-url", default=outs.get("ApiUrl"))
    p.add_argument("--stream-url", default=outs.get("StreamFunctionUrl"))
    p.add_argument("--user-pool-id", default=outs.get("UserPoolId"))
    p.add_argument("--client-id", default=outs.get("UserPoolClientId"))
    p.add_argument("--username", default=outs.get("TestUserName"))
    p.add_argument("--password-secret-arn", default=outs.get("TestUserPasswordSecretArn"))
    p.add_argument("--client-secret-arn", default=outs.get("UserPoolClientSecretArn"))
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args()

    required = ["api_url", "user_pool_id", "client_id", "username", "password_secret_arn"]
    missing = [k for k in required if getattr(args, k) is None]
    if missing:
        print(f"ERROR: missing required values: {missing}", file=sys.stderr)
        return 2

    base = args.api_url.rstrip("/")
    try:
        token = _fetch_id_token(args)
    except ClientError as e:
        print(f"ERROR: failed to fetch ID token: {e}", file=sys.stderr)
        return 2

    runner = CheckRunner()
    check_health(runner, base)
    check_query_happy(runner, base, token)
    check_query_no_token(runner, base)
    check_query_empty(runner, base, token)
    check_conversations_list(runner, base, token)
    check_query_stream(runner, args.stream_url, token)
    check_upload_ingest_flow(runner, base, token)

    print()
    if runner.failures:
        print(f"SUMMARY: {len(runner.failures)} check(s) FAILED: {', '.join(runner.failures)}")
        return 1
    print("SUMMARY: all 7 checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
