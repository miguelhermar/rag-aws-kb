#!/usr/bin/env python3
"""Start a Bedrock Knowledge Base ingestion job and poll until terminal status.

Docs consulted:
- bedrock-agent start_ingestion_job (params: knowledgeBaseId, dataSourceId; resp: ingestionJob.ingestionJobId)
  https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agent/client/start_ingestion_job.html
- bedrock-agent get_ingestion_job (params: knowledgeBaseId, dataSourceId, ingestionJobId;
  status enum: STARTING|IN_PROGRESS|COMPLETE|FAILED|STOPPING|STOPPED;
  statistics: numberOfDocumentsScanned, numberOfNewDocumentsIndexed, numberOfDocumentsFailed, ...)
  https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agent/client/get_ingestion_job.html
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
CDK_OUTPUTS = REPO_ROOT / "cdk-outputs.json"

TERMINAL_OK = {"COMPLETE"}
TERMINAL_FAIL = {"FAILED", "STOPPED"}


def _load_defaults() -> tuple[str | None, str | None]:
    if not CDK_OUTPUTS.exists():
        return None, None
    try:
        data = json.loads(CDK_OUTPUTS.read_text())
        s = data.get("StorageStack", {})
        return s.get("KbId"), s.get("DataSourceId")
    except (json.JSONDecodeError, KeyError):
        return None, None


def main() -> int:
    kb_default, ds_default = _load_defaults()

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--kb-id", default=kb_default,
                   help="Knowledge Base ID (default: StorageStack.KbId from cdk-outputs.json)")
    p.add_argument("--data-source-id", default=ds_default,
                   help="Data Source ID (default: StorageStack.DataSourceId from cdk-outputs.json)")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--timeout", type=int, default=300, help="Polling timeout in seconds (default 300)")
    p.add_argument("--interval", type=int, default=10, help="Polling interval in seconds (default 10)")
    args = p.parse_args()

    if not args.kb_id or not args.data_source_id:
        print("ERROR: --kb-id and --data-source-id required (and not in cdk-outputs.json)", file=sys.stderr)
        return 2

    client = boto3.client("bedrock-agent", region_name=args.region)

    print(f"Starting ingestion job: kb={args.kb_id} ds={args.data_source_id}")
    try:
        resp = client.start_ingestion_job(
            knowledgeBaseId=args.kb_id,
            dataSourceId=args.data_source_id,
        )
    except ClientError as e:
        print(f"ERROR: start_ingestion_job failed: {e}", file=sys.stderr)
        return 1

    job_id = resp["ingestionJob"]["ingestionJobId"]
    print(f"Started job: {job_id}")

    start = time.time()
    last_status = None
    while True:
        elapsed = int(time.time() - start)
        try:
            job = client.get_ingestion_job(
                knowledgeBaseId=args.kb_id,
                dataSourceId=args.data_source_id,
                ingestionJobId=job_id,
            )["ingestionJob"]
        except ClientError as e:
            print(f"ERROR: get_ingestion_job failed: {e}", file=sys.stderr)
            return 1

        status = job["status"]
        if status != last_status:
            print(f"[{elapsed}s] status={status}")
            last_status = status

        if status in TERMINAL_OK:
            stats = job.get("statistics", {})
            print("\nIngestion COMPLETE")
            print(f"  numberOfDocumentsScanned:     {stats.get('numberOfDocumentsScanned', 0)}")
            print(f"  numberOfNewDocumentsIndexed:  {stats.get('numberOfNewDocumentsIndexed', 0)}")
            print(f"  numberOfModifiedDocumentsIndexed: {stats.get('numberOfModifiedDocumentsIndexed', 0)}")
            print(f"  numberOfDocumentsDeleted:     {stats.get('numberOfDocumentsDeleted', 0)}")
            print(f"  numberOfDocumentsFailed:      {stats.get('numberOfDocumentsFailed', 0)}")
            return 0

        if status in TERMINAL_FAIL:
            reasons = job.get("failureReasons") or ["(no failureReasons returned)"]
            print(f"\nIngestion {status}. Reasons:")
            for r in reasons:
                print(f"  - {r}")
            return 1

        if elapsed >= args.timeout:
            print(f"\nERROR: timeout after {args.timeout}s, last status={status}", file=sys.stderr)
            return 1

        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
