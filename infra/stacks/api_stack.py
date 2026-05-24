"""ApiStack: Lambda container + API Gateway REST API with API-key auth.

Wraps the lambda/ container image into an authenticated REST endpoint backed by
the StorageStack-provided Bedrock Knowledge Base. See PLAN.md and SESSION_HANDOFF.md §12.
"""

from __future__ import annotations

import os

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from infra.stacks.storage_stack import StorageStack

# Claude 3 Haiku in us-east-1. Foundation-model ARNs have no account id.
HAIKU_MODEL_ARN = (
    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"
)

# Path to the lambda/ asset, resolved relative to this file so synth works from any CWD.
_LAMBDA_ASSET_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lambda")
)


class ApiStack(Stack):
    """Lambda + API Gateway REST API + usage plan, wired to the live KB."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        storage_stack: StorageStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Explicit log group avoids the CDK log_retention custom-resource Lambda,
        # which would otherwise add a second AWS::Lambda::Function to the stack.
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
                "KB_ID": storage_stack.knowledge_base_id,
                "MODEL_ARN": HAIKU_MODEL_ARN,
                "LOG_LEVEL": "INFO",
            },
            log_group=log_group,
        )

        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"],
                resources=[storage_stack.knowledge_base_arn],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[HAIKU_MODEL_ARN],
            )
        )

        api = apigw.RestApi(
            self,
            "RagApi",
            rest_api_name="rag-aws-api",
            description="RAG-AWS Phase 4 REST API (Lambda + Bedrock KB).",
            endpoint_configuration=apigw.EndpointConfiguration(
                types=[apigw.EndpointType.REGIONAL]
            ),
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                logging_level=apigw.MethodLoggingLevel.INFO,
                metrics_enabled=True,
                tracing_enabled=False,
            ),
            cloud_watch_role=True,
        )

        integration = apigw.LambdaIntegration(fn, proxy=True)

        health = api.root.add_resource("health")
        health.add_method("GET", integration, api_key_required=False)

        query = api.root.add_resource("query")
        query.add_method("POST", integration, api_key_required=True)

        # unsafe_unwrap() yields a {{resolve:secretsmanager:...}} dynamic ref that
        # APIGW accepts; the value is never materialized in the synth template.
        api_key = apigw.ApiKey(
            self,
            "RagApiKey",
            api_key_name="rag-aws-key",
            value=storage_stack.api_key_secret.secret_value_from_json(
                "apiKey"
            ).unsafe_unwrap(),
        )

        plan = apigw.UsagePlan(
            self,
            "RagUsagePlan",
            name="rag-aws-plan",
            throttle=apigw.ThrottleSettings(rate_limit=5, burst_limit=10),
            quota=apigw.QuotaSettings(limit=1000, period=apigw.Period.DAY),
        )
        plan.add_api_key(api_key)
        plan.add_api_stage(stage=api.deployment_stage)

        CfnOutput(
            self,
            "ApiUrl",
            value=api.url,
            export_name="RagAws-ApiUrl",
        )
        CfnOutput(
            self,
            "LambdaFunctionName",
            value=fn.function_name,
        )
        CfnOutput(
            self,
            "LogGroupName",
            value=log_group.log_group_name,
        )
