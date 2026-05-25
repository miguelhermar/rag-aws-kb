"""ApiStack: Lambda proxy + API Gateway REST API with Cognito JWT auth.

Phase 8 rewrites this stack: the API-key + UsagePlan path is removed; all mutating
routes are gated by a Cognito User Pool authorizer. The Lambda is a thin proxy that
calls bedrock-agentcore.invoke_agent_runtime for `/query` and reads Memory + DDB for
`/conversations*`. The agent owns Bedrock model + KB IAM.
"""

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
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=["http://localhost:8501"],
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

        CfnOutput(self, "ApiUrl", value=api.url, export_name="RagAws-ApiUrl")
        CfnOutput(self, "LambdaFunctionName", value=fn.function_name)
        CfnOutput(self, "LogGroupName", value=log_group.log_group_name)
