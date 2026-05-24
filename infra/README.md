# infra/ — CDK app

AWS CDK (Python) app provisioning the RAG-AWS infrastructure. Phase 2 ships **StorageStack**:
an S3 docs bucket, an Amazon S3 Vectors bucket + index, a Bedrock Knowledge Base wired to both,
and a Secrets Manager secret holding the API key.

## Required tooling

| Tool | Version |
|---|---|
| Python | 3.12+ |
| Node.js | 20+ (only needed for the `cdk` CLI) |
| AWS CDK CLI | 2.1124.x or later (`npm install -g aws-cdk@latest`) |
| aws-cdk-lib | 2.257.0 (pinned in `requirements.txt`) |
| AWS account | with Bedrock enabled in **us-east-1** |

## Manual prerequisite

Before `cdk deploy` will succeed, **enable Bedrock model access for Titan Text Embeddings V2
(`amazon.titan-embed-text-v2:0`) in the us-east-1 console**: Bedrock → Model access → Manage
model access → check Titan Embeddings V2 → Save. (Phase 4 will additionally need Claude 3
Haiku enabled.)

## Setup

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Bootstrap (once per account/region)

```bash
cdk bootstrap aws://<ACCOUNT_ID>/us-east-1
```

## Synthesize (no AWS calls)

```bash
cdk synth StorageStack
```

## Deploy

```bash
cdk deploy StorageStack --outputs-file ../cdk-outputs.json
```

The outputs file will contain `KbId`, `KbArn`, `DocsBucketName`, `DocsBucketArn`,
`VectorBucketArn`, `VectorIndexArn`, `ApiKeySecretArn`, and `DataSourceId` — consumed by
Phase 4 (`ApiStack`) and the Phase 5 operator scripts.

## Destroy

```bash
cdk destroy StorageStack
```

`autoDeleteObjects=True` empties the docs bucket; the S3 Vectors L1s honor
`RemovalPolicy.DESTROY` and clean up the index + vector bucket.

## Layout

```
infra/
  app.py                          # CDK app entry; region-locked to us-east-1
  cdk.json
  requirements.txt
  stacks/
    storage_stack.py              # this phase
  constructs/
    s3_vectors.py                 # CfnVectorBucket + CfnIndex wrapper
    bedrock_kb.py                 # CfnKnowledgeBase + CfnDataSource wrapper
```

## Locked design choices

- **Region**: `us-east-1` (S3 Vectors GA + widest Bedrock availability). Overrides via context
  or env vars raise at app load time.
- **Embedding model**: Titan Text Embeddings V2 — 1024 dim, cosine distance, float32.
- **Chunking**: fixed-size 300 tokens, 20% overlap (deterministic, cheap; sample-docs are short).
- **Encryption**: SSE-S3 on both the docs bucket and the vector bucket (KMS upgrade documented
  in the top-level README under "Production hardening").
- **KB role trust**: scoped via `aws:SourceAccount` condition. `aws:SourceArn` cannot be used
  here because the KB ARN isn't known until after the role is created.
