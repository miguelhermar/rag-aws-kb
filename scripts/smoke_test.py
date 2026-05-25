#!/usr/bin/env python3
"""Smoke test for the deployed RAG-AWS API.

Exits 0 iff:
  1. GET /health  -> 200 {"status":"ok"}
  2. POST /query (valid key, valid body) -> 200, schema-valid QueryResponse,
     non-empty sources, confidence in [0, 1].
  3. POST /query without x-api-key       -> 403
  4. POST /query with empty question     -> 400 error="InvalidRequest"

Defaults are pulled from cdk-outputs.json, matching scripts/upload_docs.py style.
The API key is fetched from Secrets Manager and never printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
CDK_OUTPUTS = REPO_ROOT / "cdk-outputs.json"

# Reuse lambda/schemas.py for pydantic validation of the response.
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


def _default_api_url() -> str | None:
    return _load_outputs().get("ApiStack", {}).get("ApiUrl")


def _default_secret_arn() -> str | None:
    return _load_outputs().get("StorageStack", {}).get("ApiKeySecretArn")


def _fetch_api_key(secret_arn: str, region: str) -> str:
    sm = boto3.client("secretsmanager", region_name=region)
    raw = sm.get_secret_value(SecretId=secret_arn)["SecretString"]
    return json.loads(raw)["apiKey"]


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


def check_query_happy(runner: CheckRunner, base: str, api_key: str) -> None:
    payload = {"question": "What is the refund window for monthly plans?", "top_k": 3}
    # Validate request shape too (catches local typos).
    QueryRequest.model_validate(payload)
    try:
        r = requests.post(
            f"{base}/query",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
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


def check_query_no_key(runner: CheckRunner, base: str) -> None:
    try:
        r = requests.post(
            f"{base}/query",
            headers={"Content-Type": "application/json"},
            json={"question": "anything", "top_k": 3},
            timeout=15,
        )
    except requests.RequestException as e:
        runner.record("POST /query (no key) -> 403", False, f"network error: {e}")
        return
    runner.record("POST /query (no key) -> 403", r.status_code == 403, f"status={r.status_code}")


def check_query_empty(runner: CheckRunner, base: str, api_key: str) -> None:
    try:
        r = requests.post(
            f"{base}/query",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"question": ""},
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


def main() -> int:
    p = argparse.ArgumentParser(description="Smoke-test the deployed RAG-AWS API.")
    p.add_argument("--api-url", default=_default_api_url(),
                   help="API base URL (default: ApiStack.ApiUrl from cdk-outputs.json)")
    p.add_argument("--secret-arn", default=_default_secret_arn(),
                   help="Secrets Manager ARN holding the API key (default: from cdk-outputs.json)")
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args()

    if not args.api_url or not args.secret_arn:
        print("ERROR: --api-url and --secret-arn required (cdk-outputs.json missing/incomplete)",
              file=sys.stderr)
        return 2

    base = args.api_url.rstrip("/")
    try:
        api_key = _fetch_api_key(args.secret_arn, args.region)
    except ClientError as e:
        print(f"ERROR: failed to fetch API key: {e}", file=sys.stderr)
        return 2

    runner = CheckRunner()
    check_health(runner, base)
    check_query_happy(runner, base, api_key)
    check_query_no_key(runner, base)
    check_query_empty(runner, base, api_key)

    print()
    if runner.failures:
        print(f"SUMMARY: {len(runner.failures)} check(s) FAILED: {', '.join(runner.failures)}")
        return 1
    print("SUMMARY: all 4 checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
