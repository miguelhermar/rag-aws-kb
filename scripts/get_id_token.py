#!/usr/bin/env python3
"""Fetch a Cognito ID token for the configured smoke-test user.

Reads UserPoolId, UserPoolClientId, TestUserName, TestUserPasswordSecretArn
from cdk-outputs.json. Calls cognito-idp.admin-initiate-auth with
ADMIN_USER_PASSWORD_AUTH and prints the ID token to stdout. The password is
never printed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def _secret_hash(username: str, client_id: str, client_secret: str) -> str:
    msg = (username + client_id).encode("utf-8")
    digest = hmac.new(client_secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
CDK_OUTPUTS = REPO_ROOT / "cdk-outputs.json"


def _load_outputs() -> dict:
    if not CDK_OUTPUTS.exists():
        return {}
    try:
        return json.loads(CDK_OUTPUTS.read_text())
    except json.JSONDecodeError:
        return {}


def _api_outputs() -> dict:
    outs = _load_outputs()
    # Cognito outputs live under AuthStack; ApiUrl lives under ApiStack.
    merged = {}
    for stack in ("ApiStack", "AuthStack"):
        merged.update(outs.get(stack, {}))
    return merged


def _fetch_password(secret_arn: str, region: str) -> str:
    sm = boto3.client("secretsmanager", region_name=region)
    raw = sm.get_secret_value(SecretId=secret_arn)["SecretString"]
    try:
        return json.loads(raw)["password"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return raw


def get_id_token(
    user_pool_id: str,
    client_id: str,
    username: str,
    password: str,
    region: str = "us-east-1",
    client_secret: str | None = None,
) -> str:
    cidp = boto3.client("cognito-idp", region_name=region)
    auth_params = {"USERNAME": username, "PASSWORD": password}
    if client_secret:
        auth_params["SECRET_HASH"] = _secret_hash(username, client_id, client_secret)
    resp = cidp.admin_initiate_auth(
        UserPoolId=user_pool_id,
        ClientId=client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters=auth_params,
    )
    return resp["AuthenticationResult"]["IdToken"]


def main() -> int:
    outs = _api_outputs()
    p = argparse.ArgumentParser(description="Print a Cognito ID token for the test user.")
    p.add_argument("--user-pool-id", default=outs.get("UserPoolId"))
    p.add_argument("--client-id", default=outs.get("UserPoolClientId"))
    p.add_argument("--username", default=outs.get("TestUserName"))
    p.add_argument("--password-secret-arn", default=outs.get("TestUserPasswordSecretArn"))
    p.add_argument("--client-secret-arn", default=outs.get("UserPoolClientSecretArn"))
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args()

    required = ("user_pool_id", "client_id", "username", "password_secret_arn")
    missing = [k for k in required if getattr(args, k) is None]
    if missing:
        print(f"ERROR: missing required values: {missing}", file=sys.stderr)
        return 2

    try:
        password = _fetch_password(args.password_secret_arn, args.region)
        client_secret = (
            _fetch_password(args.client_secret_arn, args.region)
            if args.client_secret_arn
            else None
        )
        token = get_id_token(
            args.user_pool_id, args.client_id, args.username, password,
            args.region, client_secret,
        )
    except ClientError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
