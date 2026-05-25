"""Synth-time assertions for the Phase 8 CDK stacks. No AWS calls."""

from __future__ import annotations

import os
import sys

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from infra.stacks.agent_stack import AgentStack  # noqa: E402
from infra.stacks.api_stack import ApiStack  # noqa: E402
from infra.stacks.auth_stack import AuthStack  # noqa: E402
from infra.stacks.storage_stack import StorageStack  # noqa: E402


@pytest.fixture(scope="module")
def stacks():
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    storage = StorageStack(app, "StorageStack", env=env)
    auth = AuthStack(app, "AuthStack", env=env)
    agent = AgentStack(app, "AgentStack", env=env, storage_stack=storage)
    api = ApiStack(
        app,
        "ApiStack",
        env=env,
        storage_stack=storage,
        auth_stack=auth,
        agent_stack=agent,
    )
    return storage, auth, agent, api


def test_storage_stack_has_memory_and_table(stacks):
    storage, _, _, _ = stacks
    template = Template.from_stack(storage)
    template.resource_count_is("AWS::BedrockAgentCore::Memory", 1)
    template.resource_count_is("AWS::DynamoDB::Table", 1)
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [
                {"AttributeName": "actor_id", "KeyType": "HASH"},
                {"AttributeName": "session_id", "KeyType": "RANGE"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
    )


def test_auth_stack_has_user_pool_client_and_user(stacks):
    _, auth, _, _ = stacks
    template = Template.from_stack(auth)
    template.resource_count_is("AWS::Cognito::UserPool", 1)
    template.resource_count_is("AWS::Cognito::UserPoolClient", 1)
    template.resource_count_is("AWS::Cognito::UserPoolUser", 1)


def test_agent_stack_has_runtime(stacks):
    _, _, agent, _ = stacks
    template = Template.from_stack(agent)
    template.resource_count_is("AWS::BedrockAgentCore::Runtime", 1)


def test_api_stack_has_no_api_key_or_usage_plan(stacks):
    _, _, _, api = stacks
    template = Template.from_stack(api)
    template.resource_count_is("AWS::ApiGateway::ApiKey", 0)
    template.resource_count_is("AWS::ApiGateway::UsagePlan", 0)


def test_api_stack_has_cognito_authorizer(stacks):
    _, _, _, api = stacks
    template = Template.from_stack(api)
    template.resource_count_is("AWS::ApiGateway::Authorizer", 1)
    template.has_resource_properties(
        "AWS::ApiGateway::Authorizer",
        {"Type": "COGNITO_USER_POOLS"},
    )


def test_api_stack_has_two_lambdas(stacks):
    """Phase 9a: buffered REST handler + streaming SSE handler."""
    _, _, _, api = stacks
    template = Template.from_stack(api)
    template.resource_count_is("AWS::Lambda::Function", 2)


def test_api_stack_has_streaming_function_url(stacks):
    """Phase 9a: exactly one Function URL with InvokeMode=RESPONSE_STREAM and
    CORS allowing the Streamlit origin."""
    _, _, _, api = stacks
    template = Template.from_stack(api)
    template.resource_count_is("AWS::Lambda::Url", 1)
    template.has_resource_properties(
        "AWS::Lambda::Url",
        {
            "AuthType": "NONE",
            "InvokeMode": "RESPONSE_STREAM",
            "Cors": Match.object_like(
                {
                    "AllowOrigins": ["http://localhost:8501"],
                    "AllowHeaders": Match.array_with(["Authorization", "Content-Type"]),
                }
            ),
        },
    )


def test_streaming_lambda_has_required_env(stacks):
    """Phase 9a: streaming Lambda must carry KB_ID + MODEL_ARN + Cognito ids
    so that it can both call Bedrock streaming AND verify the JWT in-handler.
    """
    _, _, _, api = stacks
    template = Template.from_stack(api)
    # Find the Lambda whose env has USER_POOL_ID (only the streaming one does).
    fns = template.find_resources("AWS::Lambda::Function")
    matching = [
        fn for fn in fns.values()
        if "USER_POOL_ID" in fn.get("Properties", {})
        .get("Environment", {})
        .get("Variables", {})
    ]
    assert len(matching) == 1, "expected exactly one Lambda with USER_POOL_ID"
    env_vars = matching[0]["Properties"]["Environment"]["Variables"]
    for key in (
        "KB_ID",
        "MODEL_ARN",
        "MODEL_ID",
        "MEMORY_ID",
        "CONVERSATIONS_TABLE",
        "USER_POOL_ID",
        "USER_POOL_CLIENT_ID",
    ):
        assert key in env_vars, f"streaming Lambda missing env var {key}"


def test_lambda_env_has_agentcore_runtime_arn(stacks):
    _, _, _, api = stacks
    template = Template.from_stack(api)
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "AGENTCORE_RUNTIME_ARN": Match.any_value(),
                        "MEMORY_ID": Match.any_value(),
                        "CONVERSATIONS_TABLE": Match.any_value(),
                    }
                )
            }
        },
    )


def test_buffered_lambda_env_does_not_contain_legacy_vars(stacks):
    """Regression guard: KB_ID/MODEL_ARN/MODEL_ID belong to the agent + Phase 9a
    streaming Lambda — NOT the Phase 8 buffered REST proxy. The buffered
    handler is identified by carrying AGENTCORE_RUNTIME_ARN.
    """
    _, _, _, api = stacks
    template = Template.from_stack(api)
    for fn in template.find_resources("AWS::Lambda::Function").values():
        env_vars = (
            fn.get("Properties", {}).get("Environment", {}).get("Variables", {})
        )
        if "AGENTCORE_RUNTIME_ARN" not in env_vars:
            # streaming Lambda (or any future Lambda) — skip
            continue
        assert "KB_ID" not in env_vars
        assert "MODEL_ARN" not in env_vars
        assert "MODEL_ID" not in env_vars


def test_query_method_requires_cognito_auth(stacks):
    _, _, _, api = stacks
    template = Template.from_stack(api)
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {
            "HttpMethod": "POST",
            "AuthorizationType": "COGNITO_USER_POOLS",
        },
    )


def test_conversations_method_requires_cognito_auth(stacks):
    _, _, _, api = stacks
    template = Template.from_stack(api)
    # Both /conversations and /conversations/{session_id} GET methods exist.
    methods = template.find_resources(
        "AWS::ApiGateway::Method",
        {"Properties": {"HttpMethod": "GET", "AuthorizationType": "COGNITO_USER_POOLS"}},
    )
    assert len(methods) >= 2


def test_health_method_does_not_require_auth(stacks):
    _, _, _, api = stacks
    template = Template.from_stack(api)
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": "GET", "AuthorizationType": "NONE", "ApiKeyRequired": False},
    )
