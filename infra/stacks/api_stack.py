"""ApiStack: Lambda proxy + API Gateway REST API with Cognito JWT auth, plus
a Phase 9a streaming Lambda fronted by a Function URL.

Phase 8 (unchanged): the API-key + UsagePlan path is removed; all mutating
REST routes are gated by a Cognito User Pool authorizer. The Lambda is a thin
proxy that calls bedrock-agentcore.invoke_agent_runtime for `/query` and reads
Memory + DDB for `/conversations*`.

Phase 9a (additive): a SECOND Lambda (DockerImageFunction) built from
`../lambda_stream/`, exposed via a Function URL with InvokeMode=RESPONSE_STREAM.
The streaming Lambda uses the AWS Lambda Web Adapter (Node runtime supports
streaming natively; Python does not — Web Adapter is the supported path). It
calls bedrock-runtime.InvokeModelWithResponseStream directly, NOT AgentCore
Runtime — AgentCore Runtime exposes a buffered Invoke API, not a streaming one.
Memory + DDB writes happen inside the streaming Lambda after the stream ends.
"""

# LWA --> https://aws.amazon.com/es/blogs/compute/using-response-streaming-with-aws-lambda-web-adapter-to-optimize-performance/

from __future__ import annotations

import os

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from infra.stacks.agent_stack import AgentStack
from infra.stacks.auth_stack import AuthStack
from infra.stacks.storage_stack import StorageStack

_LAMBDA_ASSET_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lambda")
)
_LAMBDA_STREAM_ASSET_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lambda_stream")
)

# Bedrock model ids — kept in sync with infra/stacks/agent_stack.py. The
# streaming Lambda needs InvokeModelWithResponseStream IAM on the *same*
# inference profile + foundation-model fan-out regions as the non-streaming
# path. Duplicated here intentionally: agent_stack.py is in Phase 8's scope
# and we don't want this Phase 9a change to reach into it.
_HAIKU_MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"
_HAIKU_INFERENCE_PROFILE_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_HAIKU_INFERENCE_REGIONS = ("us-east-1", "us-east-2", "us-west-2")

# Phase 12 — prod Streamlit Community Cloud origin. Local dev origin is kept
# alongside so `./scripts/run_streamlit.sh` still works against prod stacks.
_STREAMLIT_CLOUD_ORIGIN = "https://rag-aws-kb.streamlit.app"
_LOCAL_DEV_ORIGIN = "http://localhost:8501"
_ALLOWED_ORIGINS = [_LOCAL_DEV_ORIGIN, _STREAMLIT_CLOUD_ORIGIN]


class ApiStack(Stack):
    """Lambda + APIGW REST API gated by Cognito JWT, fronting the AgentCore Runtime."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        storage_stack: StorageStack,
        auth_stack: AuthStack,
        agent_stack: AgentStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        log_group = logs.LogGroup(
            self,
            "RagHandlerLogGroup",
            log_group_name=f"/aws/lambda/{construct_id}-RagHandler",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        fn = lambda_.DockerImageFunction(
            self,
            "RagHandler",
            code=lambda_.DockerImageCode.from_image_asset(_LAMBDA_ASSET_DIR),
            architecture=lambda_.Architecture.X86_64,
            memory_size=1024,
            timeout=Duration.seconds(30),
            environment={
                "AGENTCORE_RUNTIME_ARN": agent_stack.agent_runtime_arn,
                "MEMORY_ID": storage_stack.memory_id,
                "CONVERSATIONS_TABLE": storage_stack.conversations_table_name,
                # Phase 9b — upload + ingestion endpoints.
                "DOCS_BUCKET": storage_stack.docs_bucket.bucket_name,
                "KB_ID": storage_stack.knowledge_base_id,
                "DATA_SOURCE_ID": storage_stack.data_source_id,
                "LOG_LEVEL": "INFO",
            },
            log_group=log_group,
        )

        # InvokeAgentRuntime on the runtime ARN + qualifier sub-resources.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[
                    agent_stack.agent_runtime_arn,
                    f"{agent_stack.agent_runtime_arn}/*",
                ],
            )
        )

        # Read memory events for /conversations/{session_id}.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                ],
                resources=[storage_stack.memory_arn],
            )
        )

        storage_stack.conversations_table.grant(fn, "dynamodb:Query")

        api = apigw.RestApi(
            self,
            "RagApi",
            rest_api_name="rag-aws-api",
            description="RAG-AWS Phase 8 REST API (Cognito-auth Lambda proxy to AgentCore Runtime).",
            endpoint_configuration=apigw.EndpointConfiguration(
                types=[apigw.EndpointType.REGIONAL]
            ),
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                logging_level=apigw.MethodLoggingLevel.INFO,
                metrics_enabled=True,
                tracing_enabled=False,
                # Phase 12 — stage backstop throttle. Per-method overrides below
                # tighten the budget-sensitive routes further (/ingest in particular).
                # Replaces the API-key UsagePlan that was removed in Phase 8.
                throttling_rate_limit=20,
                throttling_burst_limit=40,
                method_options={
                    "/health/GET": apigw.MethodDeploymentOptions(
                        throttling_rate_limit=20, throttling_burst_limit=40,
                    ),
                    "/query/POST": apigw.MethodDeploymentOptions(
                        throttling_rate_limit=10, throttling_burst_limit=20,
                    ),
                    "/conversations/GET": apigw.MethodDeploymentOptions(
                        throttling_rate_limit=10, throttling_burst_limit=20,
                    ),
                    "/conversations/{session_id}/GET": apigw.MethodDeploymentOptions(
                        throttling_rate_limit=10, throttling_burst_limit=20,
                    ),
                    "/documents/POST": apigw.MethodDeploymentOptions(
                        throttling_rate_limit=10, throttling_burst_limit=20,
                    ),
                    # /ingest triggers KB embeddings cost; lowest cap.
                    "/ingest/POST": apigw.MethodDeploymentOptions(
                        throttling_rate_limit=5, throttling_burst_limit=10,
                    ),
                    "/ingest/{job_id}/GET": apigw.MethodDeploymentOptions(
                        throttling_rate_limit=10, throttling_burst_limit=20,
                    ),
                },
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=_ALLOWED_ORIGINS,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type"],
            ),
            cloud_watch_role=True,
        )

        authorizer = apigw.CognitoUserPoolsAuthorizer(
            self,
            "CognitoAuthorizer",
            cognito_user_pools=[auth_stack.user_pool],
            identity_source="method.request.header.Authorization",
            results_cache_ttl=Duration.minutes(5),
        )

        integration = apigw.LambdaIntegration(fn, proxy=True)

        health = api.root.add_resource("health")
        health.add_method("GET", integration, api_key_required=False)

        query = api.root.add_resource("query")
        query.add_method(
            "POST",
            integration,
            authorization_type=apigw.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        conversations = api.root.add_resource("conversations")
        conversations.add_method(
            "GET",
            integration,
            authorization_type=apigw.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        conversation_by_id = conversations.add_resource("{session_id}")
        conversation_by_id.add_method(
            "GET",
            integration,
            authorization_type=apigw.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # =====================================================================
        # Phase 9b — upload + ingestion routes (REST, Cognito-authed)
        # =====================================================================
        # POST /documents       -> mint presigned PUT URL
        # POST /ingest          -> bedrock-agent.StartIngestionJob
        # GET  /ingest/{job_id} -> bedrock-agent.GetIngestionJob
        #
        # Same buffered Lambda — these are point-in-time control-plane calls,
        # not streaming. IAM:
        #   - s3:PutObject  on docs_bucket/uploads/*    (least-privilege; the
        #     signer's permissions are inherited by every presigned URL).
        #   - bedrock:StartIngestionJob / GetIngestionJob on the KB ARN.
        documents = api.root.add_resource("documents")
        documents.add_method(
            "POST",
            integration,
            authorization_type=apigw.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        ingest = api.root.add_resource("ingest")
        ingest.add_method(
            "POST",
            integration,
            authorization_type=apigw.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        ingest_by_id = ingest.add_resource("{job_id}")
        ingest_by_id.add_method(
            "GET",
            integration,
            authorization_type=apigw.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # IAM: presigned-URL signer needs PutObject on the upload prefix only.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject"],
                resources=[f"{storage_stack.docs_bucket.bucket_arn}/uploads/*"],
            )
        )

        # IAM: KB ingestion control plane. Per AWS docs the resource ARN for
        # StartIngestionJob / GetIngestionJob is the KnowledgeBase ARN (not the
        # data-source ARN — data sources are URI-pathed sub-resources).
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                ],
                resources=[storage_stack.knowledge_base_arn],
            )
        )

        CfnOutput(self, "ApiUrl", value=api.url, export_name="RagAws-ApiUrl")
        CfnOutput(self, "LambdaFunctionName", value=fn.function_name)
        CfnOutput(self, "LogGroupName", value=log_group.log_group_name)

        # =====================================================================
        # Phase 9a — streaming Lambda + Function URL (SSE)
        # =====================================================================
        # NOTE: separate asset directory `lambda_stream/`. We do NOT reuse the
        # buffered Lambda's image: the streaming image uses Lambda Web Adapter
        # + uvicorn (long-running ASGI server), an entirely different base than
        # the public.ecr.aws/lambda/python runtime used by the buffered handler.
        # Reusing a single asset across two CMD overrides would still require
        # baking both Python toolchains into one image, defeating the cache
        # benefit. See lambda_stream/Dockerfile header for the verification.
        stream_log_group = logs.LogGroup(
            self,
            "StreamHandlerLogGroup",
            log_group_name=f"/aws/lambda/{construct_id}-StreamHandler",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        stream_fn = lambda_.DockerImageFunction(
            self,
            "StreamHandler",
            code=lambda_.DockerImageCode.from_image_asset(_LAMBDA_STREAM_ASSET_DIR),
            architecture=lambda_.Architecture.X86_64,
            memory_size=1024,
            # 60s: streaming run can be longer than the buffered REST path's 30s
            # because we wait for the model's final stop event before writing
            # Memory + DDB.
            timeout=Duration.seconds(60),
            environment={
                "KB_ID": storage_stack.knowledge_base_id,
                "MODEL_ARN": _HAIKU_INFERENCE_PROFILE_ID,
                "MODEL_ID": _HAIKU_MODEL_ID,
                "MEMORY_ID": storage_stack.memory_id,
                "CONVERSATIONS_TABLE": storage_stack.conversations_table_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                "USER_POOL_CLIENT_ID": auth_stack.user_pool_client.user_pool_client_id,
                "LOG_LEVEL": "INFO",
            },
            log_group=stream_log_group,
        )

        # IAM: KB retrieve.
        stream_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"],
                resources=[storage_stack.knowledge_base_arn],
            )
        )

        # IAM: streaming model invocation on the inference profile + the
        # foundation-model fan-out regions (us-east-1 / us-east-2 / us-west-2
        # for the `us.` cross-region profile). Mirrors agent_stack.py exactly,
        # but with `InvokeModelWithResponseStream`. We also include the buffered
        # `InvokeModel` action because the conversation-name generator runs a
        # short non-streaming call for the title.
        stream_model_resources = [
            f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{_HAIKU_INFERENCE_PROFILE_ID}",
        ] + [
            f"arn:aws:bedrock:{r}::foundation-model/{_HAIKU_MODEL_ID}"
            for r in _HAIKU_INFERENCE_REGIONS
        ]
        stream_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=stream_model_resources,
            )
        )

        # IAM: AgentCore Memory (read for is_first_turn + write on completion).
        stream_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                ],
                resources=[storage_stack.memory_arn],
            )
        )

        # IAM: DDB (PutItem for first-turn metadata + Query is harmless).
        storage_stack.conversations_table.grant(
            stream_fn, "dynamodb:PutItem", "dynamodb:Query"
        )

        # Function URL: AuthType=NONE because Streamlit cannot SigV4-sign (no IAM auth); the
        # streaming Lambda re-verifies the Cognito ID token in-handler against
        # the User Pool JWKS. CORS allows the local Streamlit origin only.
        stream_function_url = stream_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=_ALLOWED_ORIGINS,
                allowed_methods=[lambda_.HttpMethod.POST, lambda_.HttpMethod.GET],
                allowed_headers=["Authorization", "Content-Type"],
                max_age=Duration.minutes(5),
            ),
        )

        CfnOutput(
            self,
            "StreamFunctionUrl",
            value=stream_function_url.url,
            export_name="RagAws-StreamFunctionUrl",
        )
        CfnOutput(self, "StreamLambdaFunctionName", value=stream_fn.function_name)
        CfnOutput(self, "StreamLogGroupName", value=stream_log_group.log_group_name)
