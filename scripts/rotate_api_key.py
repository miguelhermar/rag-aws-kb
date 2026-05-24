#!/usr/bin/env python3
"""Rotate the API key value stored in Secrets Manager.

Flow (path "a" per PLAN.md "Auth wiring"):
  1. Generate a fresh 32-char alphanumeric value.
  2. secretsmanager.put_secret_value updates the SecretString in place.
  3. Operator MUST run `cdk deploy ApiStack` from infra/ to propagate the
     new value to API Gateway.

WHY this flow (non-obvious, verified against AWS docs):
  - The CDK ApiKey was constructed via secret.secret_value_from_json("apiKey").unsafe_unwrap(),
    which emits a CloudFormation dynamic reference: {{resolve:secretsmanager:<arn>:SecretString:apiKey}}.
  - Per CloudFormation docs, a dynamic reference is resolved ONCE at the moment the resource
    is created/updated; rotating the secret does NOT push the new value into API Gateway.
  - Per AWS API Gateway docs (patch-operations.html, UpdateApiKey section), the `/value`
    path is NOT in the list of patchable paths for an ApiKey. Supported paths are
    /customerId, /description, /enabled, /labels, /name, /stages only. So there is no
    in-place SDK call to update an existing API key's value.
  - Deleting + recreating the API key out-of-band would cause CDK drift and break the
    next `cdk deploy`.
  - Therefore: rotate the secret here, then bump the ApiKey resource via `cdk deploy ApiStack`
    so CloudFormation re-resolves the dynamic reference. A pure no-op redeploy will not
    suffice — the operator must trigger a resource update (e.g., temporarily edit the
    ApiKey description in api_stack.py, deploy, revert). See scripts/README.md for the
    exact recipe.

Docs consulted:
- secretsmanager.put_secret_value
  https://docs.aws.amazon.com/boto3/latest/reference/services/secretsmanager/client/put_secret_value.html
- API Gateway patch-operations (UpdateApiKey: /value NOT supported)
  https://docs.aws.amazon.com/apigateway/latest/api/patch-operations.html
- CloudFormation dynamic references re-resolution semantics
  https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/dynamic-references-secretsmanager.html
"""
from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
CDK_OUTPUTS = REPO_ROOT / "cdk-outputs.json"

KEY_LENGTH = 32
ALPHABET = string.ascii_letters + string.digits


def _load_default_secret_arn() -> str | None:
    if not CDK_OUTPUTS.exists():
        return None
    try:
        data = json.loads(CDK_OUTPUTS.read_text())
        return data.get("StorageStack", {}).get("ApiKeySecretArn")
    except (json.JSONDecodeError, KeyError):
        return None


def _new_value(length: int = KEY_LENGTH) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--secret-arn", default=_load_default_secret_arn(),
                   help="Secrets Manager ARN (default: StorageStack.ApiKeySecretArn from cdk-outputs.json)")
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args()

    if not args.secret_arn:
        print("ERROR: --secret-arn not provided and cdk-outputs.json missing/incomplete", file=sys.stderr)
        return 2

    new_value = _new_value()
    sm = boto3.client("secretsmanager", region_name=args.region)

    try:
        sm.put_secret_value(
            SecretId=args.secret_arn,
            SecretString=json.dumps({"apiKey": new_value}),
        )
    except ClientError as e:
        print(f"ERROR: put_secret_value failed: {e}", file=sys.stderr)
        return 1

    print("Rotated. New value stored in Secrets Manager.")
    print("")
    print("To propagate to API Gateway:")
    print("  1. cd infra")
    print("  2. Trigger a resource update on the ApiKey so CFN re-resolves the dynamic reference.")
    print("     Simplest: edit api_stack.py and bump the ApiKey 'description' (or any prop).")
    print("  3. cdk deploy ApiStack")
    print("  4. Revert the cosmetic edit and re-deploy on the next change.")
    print("")
    print("Fetch the new value with:")
    print(f"  aws secretsmanager get-secret-value --secret-id {args.secret_arn[:60]}... \\")
    print("    --region us-east-1 --query SecretString --output text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
