#!/usr/bin/env python3
"""CDK app entry point. Phase 8: StorageStack + AuthStack + AgentStack + ApiStack."""

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

from infra.stacks.agent_stack import AgentStack  # noqa: E402
from infra.stacks.api_stack import ApiStack  # noqa: E402
from infra.stacks.auth_stack import AuthStack  # noqa: E402
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

storage_stack = StorageStack(
    app,
    "StorageStack",
    env=env,
    description="RAG-AWS: S3 docs bucket, S3 Vectors index, Bedrock KB, DynamoDB conversations, AgentCore Memory.",
)

auth_stack = AuthStack(
    app,
    "AuthStack",
    env=env,
    description="RAG-AWS Phase 8: Cognito User Pool + App Client + Hosted UI + test user.",
)

agent_stack = AgentStack(
    app,
    "AgentStack",
    env=env,
    storage_stack=storage_stack,
    description="RAG-AWS Phase 8: Bedrock AgentCore Runtime (container image).",
)
agent_stack.add_dependency(storage_stack)

api_stack = ApiStack(
    app,
    "ApiStack",
    env=env,
    storage_stack=storage_stack,
    auth_stack=auth_stack,
    agent_stack=agent_stack,
    description="RAG-AWS Phase 8: Lambda proxy + REST API with Cognito JWT auth.",
)
api_stack.add_dependency(storage_stack)
api_stack.add_dependency(auth_stack)
api_stack.add_dependency(agent_stack)

app.synth()
