"""StorageStack: docs bucket, S3 Vectors index, Bedrock KB, and API-key secret.

Long-lived state for the RAG service. See PLAN.md §"CDK design".

References:
  https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_s3/Bucket.html
  https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_secretsmanager/Secret.html
  https://docs.aws.amazon.com/bedrock/latest/userguide/kb-permissions.html
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

from aws_cdk import CfnOutput, CustomResource, Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrockagentcore as bedrockagentcore
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

from infra.constructs.bedrock_kb import BedrockS3VectorsKnowledgeBase
from infra.constructs.s3_vectors import S3VectorsIndex

# Repo-relative path to the sample documents that pre-seed the KB on every
# `cdk deploy`. Resolved here (not in BucketDeployment.from_asset call) so the
# same path is used for the content hash that drives ingestion-trigger semantics.
_SEED_DIR = Path(__file__).resolve().parent.parent.parent / "sample-docs"


def _seed_content_hash(seed_dir: Path) -> str:
    """SHA-256 over (filename, bytes) pairs of seed docs, sorted for determinism.

    Used as a CustomResource property so CFN sees a diff (and re-fires ingestion)
    whenever the seed corpus changes, and a no-op when it doesn't.
    """
    h = hashlib.sha256()
    for f in sorted(seed_dir.glob("*.md")):
        h.update(f.name.encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()

# Titan Text Embeddings V2, us-east-1. Foundation-model ARNs have no account id.
# https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html
TITAN_V2_MODEL_ARN = (
    "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
)


class StorageStack(Stack):
    """Provisions S3 docs bucket, S3 Vectors bucket+index, Bedrock KB, and the API-key secret."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Docs bucket (source of truth for KB ingestion) -----------------
        docs_bucket = s3.Bucket(
            self,
            "DocsBucket",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # --- S3 Vectors bucket + index --------------------------------------
        # Names must be globally unique within an account/region for S3 Vectors and
        # must be 3-63 chars lowercase. Embed the stack name + account suffix.
        suffix = self.account[-6:] if self.account and not self.account.startswith("${") else "local"
        vector_bucket_name = f"rag-aws-vectors-{suffix}".lower()
        vector_index_name = "rag-aws-kb-index"

        vector_store = S3VectorsIndex(
            self,
            "VectorStore",
            bucket_name=vector_bucket_name,
            index_name=vector_index_name,
        )

        # --- KB service role (least privilege) -------------------------------
        # Trust policy: only the Bedrock service can assume.
        # Per https://docs.aws.amazon.com/bedrock/latest/userguide/kb-permissions.html the
        # confused-deputy conditions (aws:SourceAccount / aws:SourceArn) are recommended;
        # SourceArn for the KB cannot be known before creation, so we scope by SourceAccount.
        kb_role = iam.Role(
            self,
            "KnowledgeBaseRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description="Service role assumed by Amazon Bedrock for the RAG knowledge base.",
        )

        # Read docs bucket
        kb_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[docs_bucket.bucket_arn, f"{docs_bucket.bucket_arn}/*"],
            )
        )

        # Full access to its own vector bucket + index (scoped by ARN).
        # s3vectors:* is the documented set for KB integration; tightening to the precise
        # verb list (GetVectors/PutVectors/QueryVectors/DeleteVectors/ListVectors) can be
        # done once the boto3 docs settle. Wildcard is scoped to OUR bucket/index only.
        kb_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["s3vectors:*"],
                resources=[vector_store.bucket_arn, vector_store.index_arn],
            )
        )

        # Invoke the Titan embedding model only.
        kb_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[TITAN_V2_MODEL_ARN],
            )
        )

        # --- Bedrock Knowledge Base + DataSource -----------------------------
        kb = BedrockS3VectorsKnowledgeBase(
            self,
            "KB",
            kb_name="rag-aws-kb",
            kb_role=kb_role,
            embedding_model_arn=TITAN_V2_MODEL_ARN,
            vector_index_arn=vector_store.index_arn,
            vector_bucket_arn=vector_store.bucket_arn,
            docs_bucket=docs_bucket,
        )

        # --- Conversations table (Phase 8) -----------------------------------
        conversations_table = dynamodb.Table(
            self,
            "ConversationsTable",
            partition_key=dynamodb.Attribute(
                name="actor_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="session_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- AgentCore Memory (Phase 8) --------------------------------------
        # 30 day raw-event retention. No strategies (short-term only); AgentCore
        # writes USER + ASSISTANT items per turn via CreateEvent from inside the agent.
        memory = bedrockagentcore.CfnMemory(
            self,
            "AgentMemory",
            name="rag_aws_memory",
            event_expiry_duration=30,
        )

        # --- Pre-seed: upload sample-docs/ and trigger KB ingestion ----------
        # On every `cdk deploy`, BucketDeployment syncs sample-docs/*.md into the
        # docs bucket root (matching the key shape that scripts/upload_docs.py
        # uses, so eval source-match patterns like `document=refund-policy.md`
        # stay valid). `prune=False` is critical — user uploads via Phase 9 live
        # under `uploads/{date}/...` and must NOT be wiped when seed is re-synced.
        # https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_s3_deployment/BucketDeployment.html
        seed_deployment = s3deploy.BucketDeployment(
            self,
            "SeedDocsDeployment",
            sources=[s3deploy.Source.asset(str(_SEED_DIR))],
            destination_bucket=docs_bucket,
            prune=False,
            retain_on_delete=False,
        )

        # Lambda-backed Custom Resource that calls bedrock-agent.StartIngestionJob
        # after the seed files are in S3, then polls until COMPLETE. Mirrors the
        # behaviour of scripts/start_ingestion.py. Same pattern as the password-sync
        # CR in auth_stack.py (Phase 11 lesson: dynamic-reference secrets don't
        # resolve inside Custom::AWS, so we use a real Lambda).
        seed_ingestion_fn = _lambda.Function(
            self,
            "SeedIngestionFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            timeout=Duration.minutes(5),
            log_retention=logs.RetentionDays.ONE_WEEK,
            code=_lambda.Code.from_inline(
                textwrap.dedent(
                    """
                    import json
                    import time
                    import urllib.request
                    import boto3

                    TERMINAL_OK = {"COMPLETE"}
                    TERMINAL_FAIL = {"FAILED", "STOPPED"}
                    POLL_INTERVAL_S = 5
                    POLL_DEADLINE_S = 240

                    bedrock_agent = boto3.client("bedrock-agent")

                    def handler(event, _ctx):
                        try:
                            if event["RequestType"] == "Delete":
                                return _send(event, "SUCCESS")
                            props = event["ResourceProperties"]
                            kb_id = props["KbId"]
                            ds_id = props["DataSourceId"]
                            resp = bedrock_agent.start_ingestion_job(
                                knowledgeBaseId=kb_id,
                                dataSourceId=ds_id,
                                description="cdk-seed",
                            )
                            job_id = resp["ingestionJob"]["ingestionJobId"]
                            deadline = time.time() + POLL_DEADLINE_S
                            while time.time() < deadline:
                                job = bedrock_agent.get_ingestion_job(
                                    knowledgeBaseId=kb_id,
                                    dataSourceId=ds_id,
                                    ingestionJobId=job_id,
                                )["ingestionJob"]
                                status = job["status"]
                                if status in TERMINAL_OK:
                                    stats = job.get("statistics", {}) or {}
                                    return _send(event, "SUCCESS", {
                                        "JobId": job_id,
                                        "DocumentsScanned": str(stats.get("numberOfDocumentsScanned", 0)),
                                        "DocumentsIndexed": str(stats.get("numberOfNewDocumentsIndexed", 0)),
                                        "DocumentsFailed":  str(stats.get("numberOfDocumentsFailed", 0)),
                                    })
                                if status in TERMINAL_FAIL:
                                    reasons = job.get("failureReasons") or []
                                    return _send(event, "FAILED",
                                                 reason=f"Ingestion {status}: {reasons}")
                                time.sleep(POLL_INTERVAL_S)
                            return _send(event, "FAILED",
                                         reason=f"Ingestion polling timeout after {POLL_DEADLINE_S}s")
                        except Exception as exc:
                            return _send(event, "FAILED", reason=str(exc))

                    def _send(event, status, data=None, reason=None):
                        body = json.dumps({
                            "Status": status,
                            "Reason": reason or "OK",
                            "PhysicalResourceId": event.get("PhysicalResourceId")
                                or f"seed-ingestion-{event['LogicalResourceId']}",
                            "StackId": event["StackId"],
                            "RequestId": event["RequestId"],
                            "LogicalResourceId": event["LogicalResourceId"],
                            "Data": data or {},
                        }).encode("utf-8")
                        req = urllib.request.Request(
                            event["ResponseURL"], data=body, method="PUT",
                        )
                        req.add_header("Content-Type", "")
                        req.add_header("Content-Length", str(len(body)))
                        urllib.request.urlopen(req)
                    """
                ).strip()
            ),
        )

        # Scoping per api_stack.py (Phase 9b): Start/GetIngestionJob take the
        # KnowledgeBase ARN, NOT the data-source ARN.
        seed_ingestion_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                ],
                resources=[kb.knowledge_base_arn],
            )
        )

        seed_ingestion = CustomResource(
            self,
            "SeedKnowledgeBase",
            service_token=seed_ingestion_fn.function_arn,
            properties={
                "KbId": kb.knowledge_base_id,
                "DataSourceId": kb.data_source_id,
                # ContentHash drives the update semantics: changes to any seed
                # file produce a new hash → CFN diff → re-fires the CR → KB
                # re-ingests the delta. Unchanged seed = no diff = no-op (no
                # wasted Titan embedding spend on idempotent redeploys).
                "ContentHash": _seed_content_hash(_SEED_DIR),
            },
        )
        seed_ingestion.node.add_dependency(seed_deployment)

        # --- Outputs (exact names consumed by Phase 4 / scripts) -------------
        CfnOutput(self, "KbId", value=kb.knowledge_base_id, export_name="RagAws-KbId")
        CfnOutput(self, "KbArn", value=kb.knowledge_base_arn, export_name="RagAws-KbArn")
        CfnOutput(
            self,
            "DocsBucketName",
            value=docs_bucket.bucket_name,
            export_name="RagAws-DocsBucketName",
        )
        CfnOutput(
            self,
            "DocsBucketArn",
            value=docs_bucket.bucket_arn,
            export_name="RagAws-DocsBucketArn",
        )
        CfnOutput(
            self,
            "VectorBucketArn",
            value=vector_store.bucket_arn,
            export_name="RagAws-VectorBucketArn",
        )
        CfnOutput(
            self,
            "VectorIndexArn",
            value=vector_store.index_arn,
            export_name="RagAws-VectorIndexArn",
        )
        CfnOutput(
            self,
            "DataSourceId",
            value=kb.data_source_id,
            export_name="RagAws-DataSourceId",
        )
        CfnOutput(
            self,
            "ConversationsTableName",
            value=conversations_table.table_name,
            export_name="RagAws-ConversationsTableName",
        )
        CfnOutput(
            self,
            "MemoryId",
            value=memory.attr_memory_id,
            export_name="RagAws-MemoryId",
        )
        CfnOutput(
            self,
            "MemoryArn",
            value=memory.attr_memory_arn,
            export_name="RagAws-MemoryArn",
        )

        # Expose for downstream stacks within the same app.
        self.docs_bucket = docs_bucket
        self.knowledge_base_id = kb.knowledge_base_id
        self.knowledge_base_arn = kb.knowledge_base_arn
        self.data_source_id = kb.data_source_id
        self.conversations_table = conversations_table
        self.conversations_table_name = conversations_table.table_name
        self.memory_id = memory.attr_memory_id
        self.memory_arn = memory.attr_memory_arn
