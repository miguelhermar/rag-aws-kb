#!/usr/bin/env python3
"""Eval harness: run questions.json against the deployed API and write a Markdown report.

Outputs tests/eval/eval_results.md. Same argparse defaults pattern as scripts/upload_docs.py.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

import requests
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CDK_OUTPUTS = REPO_ROOT / "cdk-outputs.json"
EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = EVAL_DIR / "questions.json"
RESULTS_PATH = EVAL_DIR / "eval_results.md"

SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from get_id_token import get_id_token, _fetch_password  # noqa: E402

POLITE_DELAY_S = 0.25
TOP_K = 5
ANSWER_TRUNCATE = 120


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


def _truncate(s: str, n: int = ANSWER_TRUNCATE) -> str:
    s = (s or "").replace("\n", " ").replace("|", "\\|").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _ask(base: str, token: str, question: str) -> tuple[dict | None, int, int]:
    """Return (body, status, latency_ms)."""
    t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{base}/query",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "question": question,
                "session_id": "s-" + uuid.uuid4().hex,
                "top_k": TOP_K,
            },
            timeout=30,
        )
    except requests.RequestException as e:
        return {"_network_error": str(e)}, 0, int((time.perf_counter() - t0) * 1000)
    latency = int((time.perf_counter() - t0) * 1000)
    try:
        body = r.json()
    except ValueError:
        body = None
    return body, r.status_code, latency


def main() -> int:
    outs = _api_outputs()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--api-url", default=outs.get("ApiUrl"))
    p.add_argument("--user-pool-id", default=outs.get("UserPoolId"))
    p.add_argument("--client-id", default=outs.get("UserPoolClientId"))
    p.add_argument("--username", default=outs.get("TestUserName"))
    p.add_argument("--password-secret-arn", default=outs.get("TestUserPasswordSecretArn"))
    p.add_argument("--client-secret-arn", default=outs.get("UserPoolClientSecretArn"))
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--questions", default=str(QUESTIONS_PATH))
    p.add_argument("--output", default=str(RESULTS_PATH))
    args = p.parse_args()

    required = ["api_url", "user_pool_id", "client_id", "username", "password_secret_arn"]
    missing = [k for k in required if getattr(args, k) is None]
    if missing:
        print(f"ERROR: missing required values: {missing}", file=sys.stderr)
        return 2

    base = args.api_url.rstrip("/")
    questions = json.loads(Path(args.questions).read_text())

    try:
        password = _fetch_password(args.password_secret_arn, args.region)
        client_secret = (
            _fetch_password(args.client_secret_arn, args.region)
            if getattr(args, "client_secret_arn", None)
            else None
        )
        token = get_id_token(
            args.user_pool_id, args.client_id, args.username, password,
            args.region, client_secret,
        )
    except ClientError as e:
        print(f"ERROR: failed to fetch ID token: {e}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    wall_start = time.perf_counter()
    for i, q in enumerate(questions):
        body, status, latency = _ask(base, token, q["question"])
        sources = (body or {}).get("sources") or []
        top = sources[0] if sources else {}
        actual_top = top.get("document") or "(none)"
        score = float(top.get("score", 0.0)) if sources else 0.0
        confidence = float((body or {}).get("confidence", 0.0))
        answer = (body or {}).get("answer", "")
        rows.append(
            {
                **q,
                "status": status,
                "actual_top_source": actual_top,
                "score": score,
                "confidence": confidence,
                "answer": answer,
                "latency_ms": latency,
            }
        )
        print(f"[{i + 1}/{len(questions)}] {q['id']} status={status} latency={latency}ms "
              f"top={actual_top} conf={confidence:.3f}")
        if i < len(questions) - 1:
            time.sleep(POLITE_DELAY_S)
    wall_ms = int((time.perf_counter() - wall_start) * 1000)

    # Aggregates.
    in_corpus = [r for r in rows if r["category"] == "in-corpus"]
    off_corpus = [r for r in rows if r["category"] == "off-corpus"]
    matches = [r for r in in_corpus if r["actual_top_source"] == r["expected_doc"]]
    source_match_rate = (len(matches) / len(in_corpus)) if in_corpus else 0.0
    mean_conf_in = statistics.mean(r["confidence"] for r in in_corpus) if in_corpus else 0.0
    mean_conf_off = statistics.mean(r["confidence"] for r in off_corpus) if off_corpus else 0.0
    total_api_ms = sum(r["latency_ms"] for r in rows)

    # Markdown.
    lines: list[str] = []
    lines.append("# RAG-AWS Eval Results")
    lines.append("")
    lines.append(f"- API: `{base}`")
    lines.append(f"- top_k: {TOP_K}")
    lines.append(f"- Questions: {len(rows)}")
    lines.append("")
    lines.append("| id | category | question | expected_doc | actual_top_source | score | confidence | answer | latency_ms |")
    lines.append("|---|---|---|---|---|---:|---:|---|---:|")
    for r in rows:
        expected = r["expected_doc"] or "(off-corpus)"
        lines.append(
            f"| {r['id']} | {r['category']} | {_truncate(r['question'], 80)} | {expected} "
            f"| {r['actual_top_source']} | {r['score']:.3f} | {r['confidence']:.3f} "
            f"| {_truncate(r['answer'])} | {r['latency_ms']} |"
        )
    lines.append("")
    lines.append("## Aggregate metrics")
    lines.append("")
    lines.append(f"- Source-match rate (in-corpus): **{source_match_rate * 100:.1f}%** "
                 f"({len(matches)}/{len(in_corpus)})")
    lines.append(f"- Mean confidence (in-corpus): **{mean_conf_in:.3f}**")
    lines.append(f"- Mean confidence (off-corpus): **{mean_conf_off:.3f}**")
    lines.append(f"- Total wall-clock: **{wall_ms} ms**")
    lines.append(f"- Total API latency (sum): **{total_api_ms} ms**")
    lines.append("")

    Path(args.output).write_text("\n".join(lines))
    print(f"\nWrote {args.output}")
    print(f"source_match_rate={source_match_rate * 100:.1f}% "
          f"mean_conf_in={mean_conf_in:.3f} mean_conf_off={mean_conf_off:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
