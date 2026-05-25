"""AgentStack: Bedrock AgentCore Runtime container + IAM role.

The agent container is built from `../agent/` via DockerImageAsset (CDK pushes
to ECR; CFN gets the resolved image URI). Memory writes (CreateEvent) and DDB
PutItem happen *inside* the agent — the API Lambda is a thin proxy.

References:
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-runtime.html
  https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_bedrockagentcore/CfnRuntime.html
"""

from __future__ import annotations

import os

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_bedrockagentcore as bedrockagentcore
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from constructs import Construct

from infra.stacks.storage_stack import StorageStack

HAIKU_MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"
HAIKU_INFERENCE_PROFILE_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
HAIKU_INFERENCE_REGIONS = ("us-east-1", "us-east-2", "us-west-2")

_AGENT_ASSET_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "agent")
)

_AGENT_RUNTIME_NAME = "rag_aws_agent"


class AgentStack(Stack):
    """ECR-backed AgentCore Runtime fronted by a least-privilege IAM role."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        storage_stack: StorageStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        image_asset = ecr_assets.DockerImageAsset(
            self,
            "AgentImage",
            directory=_AGENT_ASSET_DIR,
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        role = iam.Role(
            self,
            "AgentRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description="Service role assumed by Bedrock AgentCore for the RAG agent runtime.",
        )

        # Pull the agent image from the CDK assets ECR repo.
        image_asset.repository.grant_pull(role)

        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"],
                resources=[storage_stack.knowledge_base_arn],
            )
        )

        invoke_model_resources = [
            f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{HAIKU_INFERENCE_PROFILE_ID}",
        ] + [
            f"arn:aws:bedrock:{r}::foundation-model/{HAIKU_MODEL_ID}"
            for r in HAIKU_INFERENCE_REGIONS
        ]
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=invoke_model_resources,
            )
        )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                ],
                resources=[storage_stack.memory_arn],
            )
        )

        storage_stack.conversations_table.grant(role, "dynamodb:PutItem")

        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/*",
                ],
            )
        )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                resources=["*"],
            )
        )

        runtime = bedrockagentcore.CfnRuntime(
            self,
            "AgentRuntime",
            agent_runtime_name=_AGENT_RUNTIME_NAME,
            agent_runtime_artifact=bedrockagentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=bedrockagentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image_asset.image_uri,
                ),
            ),
            network_configuration=bedrockagentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC",
            ),
            protocol_configuration="HTTP",
            role_arn=role.role_arn,
            environment_variables={
                "KB_ID": storage_stack.knowledge_base_id,
                "MEMORY_ID": storage_stack.memory_id,
                "MODEL_ARN": HAIKU_INFERENCE_PROFILE_ID,
                "MODEL_ID": HAIKU_MODEL_ID,
                "CONVERSATIONS_TABLE": storage_stack.conversations_table_name,
                "LOG_LEVEL": "INFO",
            },
        )
        runtime.node.add_dependency(role)
        runtime.node.add_dependency(image_asset)

        CfnOutput(
            self,
            "AgentRuntimeArn",
            value=runtime.attr_agent_runtime_arn,
            export_name="RagAws-AgentRuntimeArn",
        )
        CfnOutput(
            self,
            "AgentRuntimeId",
            value=runtime.attr_agent_runtime_id,
        )

        self.agent_runtime_arn = runtime.attr_agent_runtime_arn
        self.agent_runtime_id = runtime.attr_agent_runtime_id
