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
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CDK_OUTPUTS = REPO_ROOT / "cdk-outputs.json"
EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = EVAL_DIR / "questions.json"
RESULTS_PATH = EVAL_DIR / "eval_results.md"

POLITE_DELAY_S = 0.25  # UsagePlan rate is 5 req/s; stay well under.
TOP_K = 5
ANSWER_TRUNCATE = 120


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


def _truncate(s: str, n: int = ANSWER_TRUNCATE) -> str:
    s = (s or "").replace("\n", " ").replace("|", "\\|").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _ask(base: str, api_key: str, question: str) -> tuple[dict | None, int, int]:
    """Return (body, status, latency_ms)."""
    t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{base}/query",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"question": question, "top_k": TOP_K},
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
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--api-url", default=_default_api_url())
    p.add_argument("--secret-arn", default=_default_secret_arn())
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--questions", default=str(QUESTIONS_PATH))
    p.add_argument("--output", default=str(RESULTS_PATH))
    args = p.parse_args()

    if not args.api_url or not args.secret_arn:
        print("ERROR: --api-url and --secret-arn required (cdk-outputs.json missing)", file=sys.stderr)
        return 2

    base = args.api_url.rstrip("/")
    questions = json.loads(Path(args.questions).read_text())

    try:
        api_key = _fetch_api_key(args.secret_arn, args.region)
    except ClientError as e:
        print(f"ERROR: failed to fetch API key: {e}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    wall_start = time.perf_counter()
    for i, q in enumerate(questions):
        body, status, latency = _ask(base, api_key, q["question"])
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
