#!/usr/bin/env python3
"""CDK app entry point. Phase 2: StorageStack only. ApiStack lands in Phase 4."""

from __future__ import annotations

import os
import sys

# Make `infra` an importable package (parent dir on sys.path) so we can use absolute imports
# like `infra.stacks.storage_stack`. We intentionally do NOT add `infra/` itself to sys.path,
# because `infra/constructs/` would then shadow the pypi `constructs` package that aws-cdk-lib
# depends on at import time.
_INFRA_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_INFRA_DIR))

import aws_cdk as cdk  # noqa: E402

from infra.stacks.storage_stack import StorageStack  # noqa: E402

# Region is locked: S3 Vectors GA regions are limited and Bedrock model availability is widest
# in us-east-1. Fail fast if a reviewer overrides it via context or env.
REQUIRED_REGION = "us-east-1"

app = cdk.App()

context_region = app.node.try_get_context("region")
if context_region and context_region != REQUIRED_REGION:
    raise RuntimeError(
        f"Region override '{context_region}' not supported. "
        f"This stack is pinned to {REQUIRED_REGION} (S3 Vectors + Bedrock constraint)."
    )

env_region = os.environ.get("CDK_DEPLOY_REGION") or os.environ.get("CDK_DEFAULT_REGION")
if env_region and env_region != REQUIRED_REGION:
    raise RuntimeError(
        f"CDK env region '{env_region}' not supported. "
        f"This stack is pinned to {REQUIRED_REGION}."
    )

account = (
    os.environ.get("CDK_DEPLOY_ACCOUNT")
    or os.environ.get("CDK_DEFAULT_ACCOUNT")
)

env = cdk.Environment(account=account, region=REQUIRED_REGION)

StorageStack(
    app,
    "StorageStack",
    env=env,
    description="RAG-AWS Phase 2: S3 docs bucket, S3 Vectors index, Bedrock KB, API key secret.",
)

app.synth()
