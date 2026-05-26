#!/usr/bin/env python3
"""Print the Streamlit Community Cloud secrets TOML for this project.

Phase 12 — the prod Streamlit app is hosted on Streamlit Community Cloud
(https://rag-aws-kb.streamlit.app). Streamlit Cloud manages secrets in its
own dashboard (App settings -> Secrets), not from AWS. There is no public
API to set them programmatically.

This script reads live CloudFormation outputs (AuthStack + ApiStack) plus
Secrets Manager (App Client secret), and prints a complete TOML block ready
to paste into the Streamlit Cloud Secrets editor.

Usage:
    python scripts/print_streamlit_cloud_secrets.py | pbcopy
    # then paste in Streamlit Cloud -> App settings -> Secrets -> save

After every `cdk destroy --all` + redeploy the Cognito IDs and App Client
secret rotate, so re-run this script and re-paste. (RemovalPolicy is DESTROY
by design for this project.)

The `cookie_secret` is persisted across runs in `.streamlit_cloud_cookie_secret`
(gitignored) so live Streamlit Cloud session cookies survive a re-paste.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets as pysecrets
import subprocess
import sys
from pathlib import Path

REGION = os.environ.get("AWS_REGION", "us-east-1")
REPO_ROOT = Path(__file__).resolve().parent.parent
COOKIE_SECRET_FILE = REPO_ROOT / ".streamlit_cloud_cookie_secret"
PROD_REDIRECT_URI = "https://rag-aws-kb.streamlit.app/oauth2callback"


def _aws(*args: str) -> str:
    result = subprocess.run(
        ["aws", *args, "--region", REGION, "--output", "text"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(result.returncode)
    return result.stdout.strip()


def _stack_output(stack: str, key: str) -> str:
    value = _aws(
        "cloudformation",
        "describe-stacks",
        "--stack-name",
        stack,
        "--query",
        f"Stacks[0].Outputs[?OutputKey=='{key}'].OutputValue | [0]",
    )
    if not value or value == "None":
        sys.stderr.write(
            f"error: output '{key}' not found on stack '{stack}'. "
            f"Did you run 'cd infra && cdk deploy --all'?\n"
        )
        sys.exit(1)
    return value


def _secret_string(secret_arn: str) -> str:
    return _aws(
        "secretsmanager",
        "get-secret-value",
        "--secret-id",
        secret_arn,
        "--query",
        "SecretString",
    )


def _cookie_secret() -> str:
    if COOKIE_SECRET_FILE.exists():
        existing = COOKIE_SECRET_FILE.read_text().strip()
        if len(existing) >= 64:
            return existing
    fresh = pysecrets.token_hex(32)
    COOKIE_SECRET_FILE.write_text(fresh + "\n")
    COOKIE_SECRET_FILE.chmod(0o600)
    return fresh


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--redirect-uri",
        default=PROD_REDIRECT_URI,
        help=(
            "Override the Cognito OAuth redirect_uri. Default is the prod "
            "Streamlit Cloud URL. For local dev use ./scripts/run_streamlit.sh "
            "instead, which writes the local secrets.toml directly."
        ),
    )
    args = parser.parse_args()

    sys.stderr.write(f"reading live stack outputs from CloudFormation (region {REGION})…\n")
    user_pool_id = _stack_output("AuthStack", "UserPoolId")
    client_id = _stack_output("AuthStack", "UserPoolClientId")
    client_secret_arn = _stack_output("AuthStack", "UserPoolClientSecretArn")
    api_url = _stack_output("ApiStack", "ApiUrl")
    stream_url = _stack_output("ApiStack", "StreamFunctionUrl")

    sys.stderr.write("reading App Client secret from Secrets Manager…\n")
    client_secret = _secret_string(client_secret_arn)
    cookie_secret = _cookie_secret()

    discovery_url = (
        f"https://cognito-idp.{REGION}.amazonaws.com/"
        f"{user_pool_id}/.well-known/openid-configuration"
    )

    toml_block = f"""\
[auth]
redirect_uri = "{args.redirect_uri}"
cookie_secret = "{cookie_secret}"
client_id = "{client_id}"
client_secret = {json.dumps(client_secret)}
server_metadata_url = "{discovery_url}"
expose_tokens = ["id", "access"]

[api]
api_base_url = "{api_url}"

[runtime]
api_base_url = "{api_url}"

[stream]
stream_url = "{stream_url}"
"""

    sys.stderr.write(
        "\n--- Paste everything below into Streamlit Cloud -> App settings -> Secrets ---\n\n"
    )
    sys.stdout.write(toml_block)
    sys.stderr.write(
        "\n--- end ---\n"
        f"(user pool {user_pool_id}, client {client_id})\n"
        f"cookie_secret persisted in {COOKIE_SECRET_FILE.relative_to(REPO_ROOT)}\n"
    )


if __name__ == "__main__":
    main()
