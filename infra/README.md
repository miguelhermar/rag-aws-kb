# infra/ - CDK app

AWS CDK (Python) app provisioning the current RAG-AWS architecture. The app
deploys four stacks:

1. `StorageStack`
2. `AuthStack`
3. `AgentStack`
4. `ApiStack`

Authentication is Cognito ID-token based. REST routes use an API Gateway
Cognito authorizer, and the streaming Function URL validates the same JWT in
the Lambda handler.

## Required tooling

| Tool | Version |
|---|---|
| Python | 3.12+ |
| Node.js | 20+ (needed for the `cdk` CLI and Docker image assets) |
| AWS CDK CLI | 2.1124.x or later (`npm install -g aws-cdk@latest`) |
| aws-cdk-lib | 2.257.0 (pinned in `requirements.txt`) |
| Docker | Required for Lambda and AgentCore Runtime container image assets |
| AWS account | with Bedrock enabled in `us-east-1` |

## Manual prerequisites

Before `cdk deploy` will succeed, enable Bedrock access in `us-east-1` for:

- Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`)
- Claude Haiku 4.5 through the US cross-region inference profile used by the
  app

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
cdk synth
```

## Deploy

```bash
cdk deploy --all --outputs-file ../cdk-outputs.json
```

The outputs file is consumed by operator scripts and includes values such as
`KbId`, `DataSourceId`, `DocsBucketName`, Cognito identifiers, REST API URL,
streaming Function URL, AgentCore Runtime identifiers, and relevant Secrets
Manager ARNs.

## Destroy

```bash
cdk destroy --all
```

`RemovalPolicy.DESTROY` is intentional for this demo. Destroying the stacks
removes Cognito IDs, URLs, generated secrets, the docs bucket, and the vector
store. After a redeploy, rerun the script tooling that syncs local or Streamlit
Cloud secrets from live CloudFormation outputs.

## Layout

```
infra/
  app.py                          # CDK app entry; region-locked to us-east-1
  cdk.json
  requirements.txt
  stacks/
    storage_stack.py              # docs bucket, S3 Vectors, KB, DDB, AgentCore Memory, seed ingestion
    auth_stack.py                 # Cognito User Pool, App Client, Hosted UI, demo user, password sync
    agent_stack.py                # AgentCore Runtime arm64 container from agent/
    api_stack.py                  # REST API, Cognito authorizer, buffered Lambda, streaming Function URL
  constructs/
    s3_vectors.py                 # CfnVectorBucket + CfnIndex wrapper
    bedrock_kb.py                 # CfnKnowledgeBase + CfnDataSource wrapper
```

## Stack responsibilities

### StorageStack

- S3 docs bucket for `sample-docs/*.md` and runtime uploads under `uploads/`.
- S3 Vectors bucket and index, configured for Titan v2 1024-dimensional
  embeddings with cosine distance.
- Bedrock Knowledge Base and data source with fixed-size 300-token chunking and
  20 percent overlap.
- DynamoDB `ConversationsTable` for per-user conversation metadata.
- AgentCore Memory for per-session conversational events.
- CDK-native sample-doc pre-seeding: `BucketDeployment` uploads the seed corpus,
  and a Lambda-backed `SeedKnowledgeBase` custom resource starts ingestion.
  The seed corpus hash drives update semantics, and `prune=False` preserves
  user uploads.

### AuthStack

- Cognito User Pool with Hosted UI and no self-sign-up.
- User Pool App Client with a client secret and OAuth callback support for
  local Streamlit and Streamlit Community Cloud.
- Pre-created `demo` user.
- Secrets Manager entries for the generated demo-user password and App Client
  secret.
- Lambda-backed custom resource that syncs the generated password into Cognito
  with `AdminSetUserPassword`.

### AgentStack

- Builds an arm64 container image from `agent/`.
- Deploys a Bedrock AgentCore Runtime that runs `BedrockAgentCoreApp`.
- Grants the runtime least-privilege access to retrieve from the KB, call
  Claude Haiku 4.5, write AgentCore Memory events, and write first-turn
  conversation metadata to DynamoDB.

### ApiStack

- Buffered Lambda from `lambda/` behind API Gateway REST.
- Streaming Lambda from `lambda_stream/` exposed through a Lambda Function URL
  with `InvokeMode=RESPONSE_STREAM` for SSE.
- API Gateway `CognitoUserPoolsAuthorizer` on every non-`/health` REST route.
- In-handler Cognito JWT validation for the streaming route.
- REST routes for health, buffered query, conversation list/replay, document
  upload URL creation, ingestion start, and ingestion status polling.
- Per-method throttling, CloudWatch logs, and CORS for local Streamlit and
  Streamlit Community Cloud.

## Locked design choices

- **Region**: `us-east-1` (S3 Vectors GA + widest Bedrock availability). Overrides via context
  or env vars raise at app load time.
- **Embedding model**: Titan Text Embeddings V2 - 1024 dim, cosine distance, float32.
- **LLM**: Claude Haiku 4.5 through the configured Bedrock US cross-region inference profile.
- **Auth**: Cognito User Pool ID JWTs. The JWT subject is used as the per-user
  actor ID for DynamoDB and AgentCore Memory.
- **Chunking**: fixed-size 300 tokens, 20% overlap (deterministic, cheap; sample-docs are short).
- **Encryption**: SSE-S3 on both the docs bucket and the vector bucket (KMS upgrade documented
  in the top-level README under "Production hardening").
- **KB role trust**: scoped via `aws:SourceAccount` condition. `aws:SourceArn` cannot be used
  here because the KB ARN isn't known until after the role is created.
- **Fresh deploy behavior**: sample docs are uploaded and ingested by CDK. The
  upload and ingestion scripts remain available only as manual fallback tools.
