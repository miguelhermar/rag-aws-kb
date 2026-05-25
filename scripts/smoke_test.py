#!/usr/bin/env python3
"""Smoke test for the deployed RAG-AWS API (Cognito JWT auth).

Exits 0 iff:
  1. GET  /health                                 -> 200 {"status":"ok"}
  2. POST /query (valid Bearer token)             -> 200, schema-valid QueryResponse,
                                                     non-empty sources, confidence in [0,1]
  3. POST /query (no Authorization header)        -> 401 or 403
  4. POST /query (empty question, valid token)    -> 400 error="InvalidRequest"
  5. GET  /conversations (valid Bearer token)     -> 200 + JSON array
"""
from __future__ import annotations

import argparse
import json
import sys
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

    print()
    if runner.failures:
        print(f"SUMMARY: {len(runner.failures)} check(s) FAILED: {', '.join(runner.failures)}")
        return 1
    print("SUMMARY: all 5 checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
