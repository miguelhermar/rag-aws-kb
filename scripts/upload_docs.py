#!/usr/bin/env python3
"""Upload sample-docs/*.md to the StorageStack docs S3 bucket, idempotently.

Docs consulted:
- boto3 S3 head_object / upload_file
  https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/head_object.html
  https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/upload_file.html
- S3 ETag = MD5 for non-multipart single-part uploads (our files are KB-sized)
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
CDK_OUTPUTS = REPO_ROOT / "cdk-outputs.json"


def _load_default_bucket() -> str | None:
    if not CDK_OUTPUTS.exists():
        return None
    try:
        data = json.loads(CDK_OUTPUTS.read_text())
        return data.get("StorageStack", {}).get("DocsBucketName")
    except (json.JSONDecodeError, KeyError):
        return None


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _remote_etag(s3, bucket: str, key: str) -> str | None:
    try:
        resp = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return resp["ETag"].strip('"')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bucket", default=_load_default_bucket(),
                   help="S3 docs bucket (default: StorageStack.DocsBucketName from cdk-outputs.json)")
    p.add_argument("--source-dir", default=str(REPO_ROOT / "sample-docs"),
                   help="Local directory containing *.md files")
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args()

    if not args.bucket:
        print("ERROR: --bucket not provided and cdk-outputs.json missing/incomplete", file=sys.stderr)
        return 2

    source = Path(args.source_dir)
    if not source.is_dir():
        print(f"ERROR: source dir not found: {source}", file=sys.stderr)
        return 2

    files = sorted(source.glob("*.md"))
    if not files:
        print(f"ERROR: no *.md files in {source}", file=sys.stderr)
        return 2

    s3 = boto3.client("s3", region_name=args.region)
    uploaded = skipped = 0

    for f in files:
        key = f.name
        size = f.stat().st_size
        local_md5 = _md5(f)
        remote_etag = _remote_etag(s3, args.bucket, key)

        if remote_etag == local_md5:
            print(f"[skipped]  {key} ({size} bytes)")
            skipped += 1
            continue

        try:
            s3.upload_file(str(f), args.bucket, key)
        except ClientError as e:
            print(f"ERROR: upload failed for {key}: {e}", file=sys.stderr)
            return 1
        print(f"[uploaded] {key} ({size} bytes)")
        uploaded += 1

    print(f"\n{uploaded} uploaded, {skipped} skipped, {len(files)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
