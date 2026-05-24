"""Reusable construct wrapping a Bedrock Knowledge Base backed by S3 Vectors + an S3 data source.

Verified against:
  https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_bedrock/CfnKnowledgeBase.html
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-bedrock-knowledgebase-s3vectorsconfiguration.html
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-bedrock-datasource-vectoringestionconfiguration.html

CFN property names confirmed: StorageConfiguration.Type = "S3_VECTORS",
S3VectorsConfiguration = { IndexArn, VectorBucketArn, IndexName? }.
"""

from __future__ import annotations

from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class BedrockS3VectorsKnowledgeBase(Construct):
    """Bedrock VECTOR KB wired to an S3 Vectors index plus an S3 bucket data source."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        kb_name: str,
        kb_role: iam.IRole,
        embedding_model_arn: str,
        vector_index_arn: str,
        vector_bucket_arn: str,
        docs_bucket: s3.IBucket,
        data_source_name: str | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        self._kb = bedrock.CfnKnowledgeBase(
            self,
            "KnowledgeBase",
            name=kb_name,
            role_arn=kb_role.role_arn,
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=embedding_model_arn,
                ),
            ),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="S3_VECTORS",
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    index_arn=vector_index_arn,
                    vector_bucket_arn=vector_bucket_arn,
                ),
            ),
        )
        # KB cannot be created before its service role exists.
        self._kb.node.add_dependency(kb_role)

        # Fixed-size 300-token chunking with 20% overlap. Chosen over semantic chunking because:
        #   (1) it is deterministic and cheap (no extra embedding pass during ingestion),
        #   (2) Titan v2 handles ~8k tokens easily so 300-token chunks leave ample headroom
        #       for Haiku context assembly with top_k=5,
        #   (3) sample-docs are short policy/FAQ markdown — semantic chunking offers little gain.
        self._data_source = bedrock.CfnDataSource(
            self,
            "DataSource",
            name=data_source_name or f"{kb_name}-docs",
            knowledge_base_id=self._kb.attr_knowledge_base_id,
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="S3",
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=docs_bucket.bucket_arn,
                ),
            ),
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(
                    chunking_strategy="FIXED_SIZE",
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=300,
                        overlap_percentage=20,
                    ),
                ),
            ),
            # If the docs bucket is removed/replaced, drop orphan chunks rather than fail the stack.
            data_deletion_policy="DELETE",
        )
        self._data_source.add_dependency(self._kb)

    @property
    def knowledge_base_id(self) -> str:
        return self._kb.attr_knowledge_base_id

    @property
    def knowledge_base_arn(self) -> str:
        return self._kb.attr_knowledge_base_arn

    @property
    def data_source_id(self) -> str:
        return self._data_source.attr_data_source_id
