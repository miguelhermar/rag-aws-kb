"""Reusable construct wrapping an Amazon S3 Vectors bucket plus a single vector index.

Verified against aws-cdk-lib 2.257.0 native L1s:
  https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_s3vectors.html
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-s3vectors-vectorbucket.html
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-s3vectors-index.html

S3 Vectors went GA Jan 2026 and the L1s (CfnVectorBucket, CfnIndex) shipped in
aws-cdk-lib shortly after, so no AwsCustomResource fallback is needed.
"""

from __future__ import annotations

from aws_cdk import RemovalPolicy
from aws_cdk import aws_s3vectors as s3vectors
from constructs import Construct


class S3VectorsIndex(Construct):
    """S3 Vectors bucket + index sized for Titan Text Embeddings V2 (1024 dim, cosine, float32)."""

    # Bedrock KB integration requires these two metadata keys to be excluded from
    # filterable metadata so the KB can stash the chunk text + source pointer.
    # https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-vector.html
    _BEDROCK_RESERVED_METADATA_KEYS = [
        "AMAZON_BEDROCK_TEXT",
        "AMAZON_BEDROCK_METADATA",
    ]

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        bucket_name: str,
        index_name: str,
        dimension: int = 1024,
        distance_metric: str = "cosine",
        data_type: str = "float32",
        removal_policy: RemovalPolicy = RemovalPolicy.DESTROY,
    ) -> None:
        super().__init__(scope, construct_id)

        self._vector_bucket = s3vectors.CfnVectorBucket(
            self,
            "VectorBucket",
            vector_bucket_name=bucket_name,
            # SSE-S3 is the default when encryption_configuration is omitted.
        )
        self._vector_bucket.apply_removal_policy(removal_policy)

        self._vector_index = s3vectors.CfnIndex(
            self,
            "VectorIndex",
            vector_bucket_name=bucket_name,
            index_name=index_name,
            data_type=data_type,
            dimension=dimension,
            distance_metric=distance_metric,
            metadata_configuration=s3vectors.CfnIndex.MetadataConfigurationProperty(
                non_filterable_metadata_keys=self._BEDROCK_RESERVED_METADATA_KEYS,
            ),
        )
        self._vector_index.add_dependency(self._vector_bucket)
        self._vector_index.apply_removal_policy(removal_policy)

        self._bucket_name = bucket_name
        self._index_name = index_name

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    @property
    def index_name(self) -> str:
        return self._index_name

    @property
    def bucket_arn(self) -> str:
        return self._vector_bucket.attr_vector_bucket_arn

    @property
    def index_arn(self) -> str:
        return self._vector_index.attr_index_arn
