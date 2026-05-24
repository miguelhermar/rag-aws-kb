"""Synth-time assertions for the CDK stacks. No AWS calls."""

from __future__ import annotations

import os
import sys

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from infra.stacks.api_stack import ApiStack  # noqa: E402
from infra.stacks.storage_stack import StorageStack  # noqa: E402


@pytest.fixture(scope="module")
def stacks():
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    storage = StorageStack(app, "StorageStack", env=env)
    api = ApiStack(app, "ApiStack", env=env, storage_stack=storage)
    return storage, api


def test_api_stack_has_single_lambda(stacks):
    _, api = stacks
    template = Template.from_stack(api)
    template.resource_count_is("AWS::Lambda::Function", 1)


def test_api_stack_has_single_rest_api(stacks):
    _, api = stacks
    template = Template.from_stack(api)
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)


def test_lambda_env_has_kb_id_and_model_arn(stacks):
    _, api = stacks
    template = Template.from_stack(api)
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "KB_ID": Match.any_value(),
                        "MODEL_ARN": Match.string_like_regexp(
                            r"^arn:aws:bedrock:us-east-1::foundation-model/anthropic\.claude-3-haiku.*"
                        ),
                    }
                )
            }
        },
    )


def test_query_method_requires_api_key(stacks):
    _, api = stacks
    template = Template.from_stack(api)
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": "POST", "ApiKeyRequired": True},
    )


def test_health_method_does_not_require_api_key(stacks):
    _, api = stacks
    template = Template.from_stack(api)
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": "GET", "ApiKeyRequired": False},
    )
