"""Synth-time assertions for the Phase 8 CDK stacks. No AWS calls."""

from __future__ import annotations

import json
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


def test_storage_stack_has_seed_bucket_deployment(stacks):
    """Pre-seed: BucketDeployment custom resource pushes sample-docs/ to the
    docs bucket on every deploy (`prune=False` so user uploads survive)."""
    storage, _, _, _ = stacks
    template = Template.from_stack(storage)
    # CDK BucketDeployment renders as Custom::CDKBucketDeployment<hash>.
    custom_resources = template.find_resources("AWS::CloudFormation::CustomResource")
    custom_resources.update(template.find_resources("Custom::CDKBucketDeployment"))
    # Also scan all resource types prefixed Custom::CDKBucketDeployment*.
    all_resources = template.to_json().get("Resources", {})
    deploy_resources = [
        r for r, body in all_resources.items()
        if body.get("Type", "").startswith("Custom::CDKBucketDeployment")
    ]
    assert len(deploy_resources) >= 1, (
        "expected at least one Custom::CDKBucketDeployment resource for seed docs"
    )
    # Prune must be False to preserve Phase 9 user uploads under uploads/*.
    for r in deploy_resources:
        props = all_resources[r].get("Properties", {})
        assert props.get("Prune") is False, (
            "SeedDocsDeployment must use Prune=False to preserve user uploads"
        )


def test_storage_stack_has_seed_ingestion_custom_resource(stacks):
    """Pre-seed: CustomResource that calls bedrock-agent.StartIngestionJob after
    the BucketDeployment finishes, with a content-hash property so unchanged
    redeploys are no-ops."""
    storage, _, _, _ = stacks
    template = Template.from_stack(storage)
    found = False
    for body in template.to_json().get("Resources", {}).values():
        if body.get("Type") != "AWS::CloudFormation::CustomResource":
            continue
        props = body.get("Properties", {})
        if "ContentHash" in props and "KbId" in props and "DataSourceId" in props:
            found = True
            break
    assert found, (
        "expected a CustomResource with KbId+DataSourceId+ContentHash for seed ingestion"
    )


def test_seed_ingestion_lambda_has_bedrock_ingestion_actions(stacks):
    """Pre-seed: the seed-ingestion Lambda must hold StartIngestionJob +
    GetIngestionJob scoped to the KB ARN (not the data-source ARN)."""
    storage, _, _, _ = stacks
    template = Template.from_stack(storage)
    found_start = found_get = False
    for pol in template.find_resources("AWS::IAM::Policy").values():
        for stmt in (
            pol.get("Properties", {}).get("PolicyDocument", {}).get("Statement", [])
        ):
            actions = stmt.get("Action")
            if isinstance(actions, str):
                actions = [actions]
            if "bedrock:StartIngestionJob" in (actions or []):
                found_start = True
            if "bedrock:GetIngestionJob" in (actions or []):
                found_get = True
    assert found_start, "seed-ingestion Lambda missing bedrock:StartIngestionJob"
    assert found_get, "seed-ingestion Lambda missing bedrock:GetIngestionJob"


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
                    "AllowOrigins": [
                        "http://localhost:8501",
                        "https://rag-aws-kb.streamlit.app",
                    ],
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
    """Regression guard: MODEL_ARN/MODEL_ID belong to the agent + Phase 9a
    streaming Lambda — NOT the Phase 8 buffered REST proxy. The buffered
    handler is identified by carrying AGENTCORE_RUNTIME_ARN.

    NOTE: as of Phase 9b the buffered Lambda DOES carry KB_ID (needed for
    bedrock-agent.StartIngestionJob), so KB_ID is no longer in this denylist.
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
        assert "MODEL_ARN" not in env_vars
        assert "MODEL_ID" not in env_vars


# ---------------------------------------------------------------------------
# Phase 9b — upload + ingestion route assertions
# ---------------------------------------------------------------------------

def test_api_stack_has_phase9b_routes(stacks):
    """Phase 9b adds POST /documents, POST /ingest, GET /ingest/{job_id}."""
    _, _, _, api = stacks
    template = Template.from_stack(api)
    methods = template.find_resources("AWS::ApiGateway::Method")
    # Existing 4 routes: GET /health, POST /query, GET /conversations,
    # GET /conversations/{session_id}. Phase 9b adds 3 more = 7 total
    # (plus the implicit OPTIONS preflights — count those separately).
    real_methods = [
        m for m in methods.values()
        if m.get("Properties", {}).get("HttpMethod") != "OPTIONS"
    ]
    assert len(real_methods) == 7, (
        f"expected 7 non-OPTIONS methods (4 existing + 3 Phase 9b), "
        f"got {len(real_methods)}"
    )


def test_buffered_lambda_has_phase9b_env(stacks):
    """Phase 9b: buffered Lambda must carry DOCS_BUCKET + KB_ID + DATA_SOURCE_ID."""
    _, _, _, api = stacks
    template = Template.from_stack(api)
    fns = template.find_resources("AWS::Lambda::Function")
    matching = [
        fn for fn in fns.values()
        if "AGENTCORE_RUNTIME_ARN" in fn.get("Properties", {})
        .get("Environment", {})
        .get("Variables", {})
    ]
    assert len(matching) == 1, "expected exactly one buffered Lambda"
    env_vars = matching[0]["Properties"]["Environment"]["Variables"]
    for key in ("DOCS_BUCKET", "KB_ID", "DATA_SOURCE_ID"):
        assert key in env_vars, f"buffered Lambda missing env var {key}"


def test_buffered_lambda_has_s3_putobject_on_uploads_prefix(stacks):
    """Phase 9b: buffered Lambda IAM must grant s3:PutObject on uploads/* only."""
    _, _, _, api = stacks
    template = Template.from_stack(api)
    policies = template.find_resources("AWS::IAM::Policy")
    found = False
    for pol in policies.values():
        statements = (
            pol.get("Properties", {}).get("PolicyDocument", {}).get("Statement", [])
        )
        for stmt in statements:
            actions = stmt.get("Action")
            if isinstance(actions, str):
                actions = [actions]
            if not actions or "s3:PutObject" not in actions:
                continue
            resources = stmt.get("Resource")
            if isinstance(resources, list):
                # CDK renders cross-stack references as Fn::Join'd lists.
                rendered = json.dumps(resources)
            else:
                rendered = json.dumps(resources or "")
            if "/uploads/*" in rendered:
                found = True
                break
        if found:
            break
    assert found, "expected s3:PutObject statement scoped to /uploads/*"


def test_buffered_lambda_has_bedrock_ingestion_actions(stacks):
    """Phase 9b: buffered Lambda IAM must grant StartIngestionJob + GetIngestionJob."""
    _, _, _, api = stacks
    template = Template.from_stack(api)
    policies = template.find_resources("AWS::IAM::Policy")
    actions_found: set[str] = set()
    for pol in policies.values():
        statements = (
            pol.get("Properties", {}).get("PolicyDocument", {}).get("Statement", [])
        )
        for stmt in statements:
            actions = stmt.get("Action")
            if isinstance(actions, str):
                actions = [actions]
            for a in actions or []:
                if a in ("bedrock:StartIngestionJob", "bedrock:GetIngestionJob"):
                    actions_found.add(a)
    assert "bedrock:StartIngestionJob" in actions_found
    assert "bedrock:GetIngestionJob" in actions_found


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
