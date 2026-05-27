# RAG-AWS Productionization — Implementation Plan

> This is the canonical implementation plan, current as of 2026-05-26 (Phase 14 — CDK-native KB pre-seeding).
> For the phase-by-phase build log + live AWS resource IDs, see [SESSION_HANDOFF.md](SESSION_HANDOFF.md).
> For the reviewer-facing overview, see [README.md](README.md).

## Context

The starting point was a working RAG prototype: a single-process Streamlit app that did Gemini embeddings + FAISS retrieval + Gemini generation, plus a partial FastAPI/Next.js stack. Single-user, no IaC, no auth, leaked Gemini key in `.env`. Preserved under [legacy/](legacy/) for diff.

The deliverable is an **AWS-native, CDK-deployed, authenticated, source-grounded RAG service** queried from a local Streamlit client over HTTPS — under a **$20 hard budget**, no OpenSearch Serverless, no SageMaker, per the [original brief](AWS%20Native%20Knowledge%20Base%20Agent%20Candidate%20Project%20Brief.md).

The implementation went beyond the brief minimum by electing five of the listed optional extensions: Cognito JWT auth, DynamoDB chat history, Amazon Bedrock AgentCore Runtime, AgentCore Memory, streaming responses, and an upload + ingestion endpoint.

## Locked decisions

| Area | Choice | Why |
|---|---|---|
| Compute (request path) | **Lambda** (container images) — one buffered (x86_64, RIC) for REST routes; one streaming (x86_64, LWA + uvicorn) for SSE | Free-tier-friendly; honors $20 cap. ECS/Fargate documented as production path for sustained throughput. |
| Agent runtime | **Amazon Bedrock AgentCore Runtime** (arm64 container, `BedrockAgentCoreApp`) invoked from the buffered Lambda via `InvokeAgentRuntime` | Demonstrates the AgentCore primitives explicitly mentioned in the brief as an acceptable orchestration layer. Adds managed memory + console observability + a "managed agent" story even though the RAG itself is single-tool. Tradeoff acknowledged in [README §1.2](README.md). |
| Retrieval | **Bedrock Knowledge Base** backed by **S3 Vectors** (GA Jan 2026) | AWS-native, no OSS/Aurora/Pinecone, lowest idle cost. |
| LLM | **Claude Haiku 4.5** via Bedrock cross-region US inference profile (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) | Brief recommended Claude 3 Haiku, but it became LEGACY post-2026 + Marketplace-gated. Re-locked 2026-05-24. Inference profile fans across `us-east-1`/`us-east-2`/`us-west-2` for free failover. |
| Embeddings | **Titan Text Embeddings V2** (`amazon.titan-embed-text-v2:0`), 1024 dims | Required by S3 Vectors + KB. |
| Auth | **Cognito User Pool + JWT** (App Client with secret, Hosted UI on `ragkb-{account-suffix}`) | Brief lists Cognito/JWT as an acceptable approach. Per-user identity is required because `actor_id = JWT.sub` is the DynamoDB partition key + AgentCore Memory `actorId`. The API-key + UsagePlan path used in Phases 1-7 was removed entirely in Phase 8. |
| Chat history | **DynamoDB metadata + AgentCore Memory messages** — DDB stores `(actor_id, session_id, conversation_name, created_at)` for sidebar listing; AgentCore Memory stores message events in native `conversational` shape | Splits "fast index" (DDB Query) from "rich payload" (Memory `ListEvents`) without overloading either. AgentCore Memory is console-introspectable, unlike msgpack blobs. |
| Streaming transport | **Lambda Function URL + SSE** (`InvokeMode=RESPONSE_STREAM`) using AWS Lambda Web Adapter + FastAPI/uvicorn | Python Lambda has no native response streaming as of May 2026; LWA + ASGI inside the container is the only AWS-native path. REST `/query` stays in parallel so smoke + eval remain deterministic. |
| Streaming source | **`bedrock-runtime.invoke_model_with_response_stream` directly** | AgentCore Runtime's `InvokeAgentRuntime` is buffered-only as of May 2026. The streaming Lambda intentionally bypasses AgentCore Runtime and writes Memory + DDB itself after stream completion — keeps behavior byte-identical to `/query`. Duplication of `agent/rag.py` ↔ `lambda_stream/stream_app.py` is an explicit tradeoff. |
| Upload mechanism | **Presigned S3 PUT URL** (SigV4, 15-min TTL, signed `Content-Type`) | No Lambda payload limit, no Lambda compute cost per byte. Client PUTs directly to S3. |
| Ingestion trigger | **Explicit `POST /ingest`** with idempotent `client_token`; **`GET /ingest/{job_id}`** polling | Gives the user feedback + explicit control over cost. Event-driven trigger (S3 PutObject → EventBridge → StartIngestionJob) is documented as a complement on the hardening list, not built. |
| IaC | **AWS CDK in Python** (`aws-cdk-lib==2.257.0`, 4 stacks, all changes go through source — never via console/CLI) | Per brief. Four-stack split lets the slow arm64 image build only re-run when agent code changes. |
| Region | **`us-east-1`** (hard-pinned in [infra/app.py](infra/app.py)) | S3 Vectors GA region + widest Bedrock model availability. |
| Legacy code | Moved to [legacy/](legacy/) | Preserves diff for "changes from sample" narrative without polluting root. |

## Target architecture (4 stacks, current as of Phase 12)

```
LOCAL                                          AWS (us-east-1)
┌────────────────────────┐  HTTPS (Authorization: Bearer <Cognito ID JWT>)
│ Streamlit              │ ──────────► ┌─────────────────────────────────┐
│ streamlit_client/      │             │ Cognito User Pool + Hosted UI   │
│  • st.login(Cognito)   │ ◄──── JWT ─ │  • App Client with secret       │
│  • Stream toggle       │             │  • Pre-created `demo` user      │
│  • File uploader       │             └─────────────────────────────────┘
│  • Sidebar history     │
└──────┬─────────────────┘
       │  REST: POST /query, /conversations*, /documents, /ingest, /ingest/{id}
       │  REST: GET  /health (no auth)
       │  SSE:  POST /query-stream (Function URL)
       │
       ▼
┌─────────────────────────────┐    ┌────────────────────────────┐
│ API Gateway REST (regional) │    │ Lambda Function URL (SSE)  │
│  CognitoUserPoolsAuthorizer │    │  AuthType=NONE             │
│  on every non-/health route │    │  InvokeMode=RESPONSE_STREAM│
└──────┬──────────────────────┘    │  In-handler JWT verify     │
       │                           │  (Cognito JWKS, pyjwt)     │
       │  AWS_PROXY                └──────┬─────────────────────┘
       ▼                                  │
┌─────────────────────────────┐          │
│ Buffered Lambda             │          │
│  lambda/  (x86_64, RIC)     │          │
│  • routes 7 endpoints       │          │
│  • proxies /query to        │          │
│    AgentCore Runtime        │          │
│  • /conversations* reads    │          │
│    Memory + DDB             │          │
│  • /documents mints         │          │
│    presigned URL            │          │
│  • /ingest* calls           │          │
│    bedrock-agent SDK        │          │
└──┬───┬───────────────────┬──┘          │
   │   │                   │             │
   │   ▼                   ▼             │
   │ ┌───────────────────────────────┐   │
   │ │ Bedrock AgentCore Runtime     │   │
   │ │  arm64 container from agent/  │   │
   │ │  BedrockAgentCoreApp:         │   │
   │ │   • Retrieve (KB)             │   │
   │ │   • InvokeModel (Haiku 4.5)   │   │
   │ │   • CreateEvent on Memory     │   │
   │ │   • PutItem on DDB (turn 1)   │   │
   │ └──┬───────────────────┬────────┘   │
   │    │                   │            │
   │    │  Retrieve         │  Invoke    │  invoke_model_with_response_stream
   │    │                   │  Model     │  + writes Memory + DDB itself
   │    ▼                   ▼            ▼
   │  ┌──────────────────┐ ┌──────────────────────┐
   │  │ Bedrock KB       │ │ Bedrock Runtime      │
   │  │  Titan v2 embed  │ │  Claude Haiku 4.5    │
   │  │  S3 Vectors      │ │  (US cross-region    │
   │  │  FIXED_SIZE 300t │ │   inference profile) │
   │  └────┬─────────────┘ └──────────────────────┘
   │       │
   │       ▼
   │  ┌──────────────────────┐  ◄── bedrock-agent.StartIngestionJob
   │  │ S3 Vectors           │      (bootstrap + per-upload)
   │  │  dim=1024 cosine     │
   │  └──────────────────────┘
   │
   ▼ (presigned PUT URL, client-to-S3, no Lambda hop)
┌──────────────────────────────────┐
│ S3 docs bucket                   │
│  sample-docs/*.md (bootstrap)    │
│  uploads/{yyyy-mm-dd}/{uuid}-…   │
└──────────────────────────────────┘

AgentCore Memory             DynamoDB ConversationsTable
 native conversational        PK actor_id, SK session_id
 EventExpiryDuration=30d      attrs conversation_name, created_at
 ListEvents (paginated)       PAY_PER_REQUEST

Secrets Manager:  App Client secret + test-user password
CloudWatch Logs:  per-Lambda structured JSON, APIGW access logs, 30-day retention
```

## Repo layout

```
infra/                                CDK app (4 stacks)
  app.py                              StorageStack → AuthStack → AgentStack → ApiStack
  cdk.json
  requirements.txt
  stacks/
    storage_stack.py                  S3 docs, S3 Vectors, KB, DDB ConversationsTable, AgentCore Memory
    auth_stack.py                     Cognito User Pool, App Client (with secret), Hosted UI, test user
    agent_stack.py                    AgentCore Runtime (arm64 container from agent/), runtime IAM role
    api_stack.py                      REST API + Cognito authorizer + buffered Lambda + streaming Lambda + Function URL + 7 routes + IAM
  constructs/
    s3_vectors.py                     CfnVectorBucket + CfnIndex
    bedrock_kb.py                     CfnKnowledgeBase + CfnDataSource

lambda/                               Buffered REST handler (x86_64, RIC)
  Dockerfile                          public.ecr.aws/lambda/python:3.12
  requirements.txt
  app.py                              Routes 7 endpoints; pydantic validation; structlog JSON
  schemas.py                          Request/response/error pydantic models
  conversations.py                    /conversations* (DDB Query + Memory ListEvents)
  uploads.py                          /documents (presigned PUT) + /ingest + /ingest/{id}

lambda_stream/                        Streaming handler (x86_64, LWA + FastAPI/uvicorn)
  Dockerfile                          python:3.12-slim + AWS Lambda Web Adapter
  requirements.txt
  stream_app.py                       FastAPI app, /query-stream SSE, in-handler JWT verify, Memory + DDB writes after stream
  schemas.py                          Copy of lambda/schemas.py (Docker contexts are isolated by design)

agent/                                AgentCore Runtime container (arm64)
  Dockerfile                          BedrockAgentCoreApp base
  requirements.txt
  app.py                              BedrockAgentCoreApp entry point
  rag.py                              Retrieve + InvokeModel + Memory + DDB writes
  schemas.py                          Local copy

streamlit_client/                     Local client (chat + sidebar + uploader)
  app.py                              st.login(Cognito) → bearer token on every call; streaming toggle; file upload status panel
  requirements.txt
  .streamlit/secrets.toml.example     Template; the real file is auto-synced by scripts/run_streamlit.sh

sample-docs/                          6 Markdown docs (Acme Notes brand) — bootstrap corpus
  refund-policy.md  shipping-policy.md  security-policy.md
  employee-handbook.md  product-faq.md  acceptable-use.md

scripts/                              Operator tooling
  run_streamlit.sh                    Phase 10 launcher — auto-syncs secrets.toml from live CFN + Secrets Manager
  upload_docs.py                      MD5-idempotent sync of sample-docs/ to S3
  start_ingestion.py                  StartIngestionJob + poll
  smoke_test.py                       7 checks: health, query happy, no-token 401, empty 400, conversations, SSE, upload+ingest 90s
  get_id_token.py                     ADMIN_USER_PASSWORD_AUTH helper for smoke + eval
  README.md

tests/                                Unit + synth + eval
  conftest.py
  test_schemas.py                     pydantic round-trips
  test_agent.py                       agent/rag.py with botocore Stubber + mocked AgentCore
  test_stream.py                      lambda_stream JWT verify + SSE framing + Claude stream iteration
  test_uploads.py                     lambda/uploads.py (presign, sanitize, MIME check, ConflictException)
  test_synth.py                       CDK synth invariants (2 Lambdas, Function URL with RESPONSE_STREAM, 7 routes, IAM scoping)
  eval/
    questions.json                    8 questions: 6 in-corpus, 1 ambiguous, 1 off-corpus
    run_eval.py                       Harness — writes eval_results.md
    eval_results.md                   Committed sample run

legacy/                               Original Gemini + FAISS + Streamlit prototype
README.md                             Reviewer-facing entry point
SESSION_HANDOFF.md                    Per-phase build log + live IDs + gotchas
PLAN.md                               This file
```

## CDK design

Four stacks in [infra/stacks/](infra/stacks/) with in-memory cross-stack refs (NOT `Fn.import_value` — all live in the same `cdk.App`). Deploy order:

**1. StorageStack** — long-lived state
- `docs_bucket` (S3, versioned, SSE-S3, block-public, `autoDeleteObjects=True`)
- `vector_bucket` + `vector_index` (`CfnVectorBucket` + `CfnIndex`, dim=1024, cosine, float32) — wraps L1 in [infra/constructs/s3_vectors.py](infra/constructs/s3_vectors.py)
- KB service IAM role with `s3:Get*` on docs bucket, `s3vectors:*` on vector index, `bedrock:InvokeModel` on Titan v2 ARN only
- `CfnKnowledgeBase` (`storageConfiguration.type = "S3_VECTORS"`) + `CfnDataSource` (FIXED_SIZE 300 tokens, 20% overlap)
- `aws_dynamodb.Table` `ConversationsTable` (PK `actor_id`, SK `session_id`, PAY_PER_REQUEST, `RemovalPolicy.DESTROY`)
- `CfnMemory` (AgentCore Memory, `EventExpiryDuration=30 days`, no semantic strategies)
- Outputs: `KbId`, `KbArn`, `DataSourceId`, `DocsBucketName`, `DocsBucketArn`, `VectorBucketArn`, `VectorIndexArn`, `ConversationsTableName`, `MemoryId`, `MemoryArn`

**2. AuthStack** — Cognito identity
- `UserPool` (no self-sign-up, password policy 12+ chars, MFA off for demo, account recovery off)
- `UserPoolClient` (App Client with secret, `OAuthFlows.authorization_code_grant`, scopes `openid email profile`, callbacks `http://localhost:8501/oauth2callback`, token lifetimes: ID/access 1h, refresh 30d)
- `UserPoolDomain` (`ragkb-{account-suffix}` — Cognito reserves `aws`/`amazon`/`cognito` prefixes)
- `CfnUserPoolUser` `demo` (`MessageAction=SUPPRESS`, attributes `email=demo@acmenotes.example`)
- Test-user password generated into Secrets Manager; **a Lambda-backed Custom Resource** reads the secret via boto3 at runtime and calls `AdminSetUserPassword` (Phase 11 hardening fix — closed the gotcha that previously required a manual `aws cognito-idp admin-set-user-password` after every fresh deploy). The original `AwsCustomResource` + `secret_value.unsafe_unwrap()` pattern is broken in this context because CFN [does not resolve `secretsmanager` dynamic refs](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/dynamic-references-secretsmanager.html) inside Custom Resource properties — the literal `{{resolve:...}}` string was being passed as the password.
- App Client secret copied into a Secrets Manager secret so it can be referenced by the launcher (Cognito doesn't expose the secret as a CFN output otherwise)
- Outputs: `UserPoolId`, `UserPoolArn`, `UserPoolClientId`, `UserPoolClientSecretArn`, `TestUserPasswordSecretArn`, `UserPoolDomain`, `OAuthDiscoveryUrl`, `TestUserName`

**3. AgentStack** — AgentCore Runtime
- `DockerImageAsset` built from [agent/](agent/) with **`platform=ecr_assets.Platform.LINUX_ARM64`** (AgentCore Runtime is arm64-only — caught live in Phase 8)
- Runtime IAM role: `bedrock-agent-runtime:Retrieve` on KB ARN, `bedrock:InvokeModel` on Haiku inference profile + 3 fan-out region foundation-model ARNs, `bedrock-agentcore:*Event*` on Memory, `dynamodb:PutItem`+`Query` on `ConversationsTable`, `logs:*`
- `CfnRuntime` (AWS::BedrockAgentCore::Runtime, container URI = `DockerImageAsset.image_uri`, env passes `KB_ID`, `MEMORY_ID`, `CONVERSATIONS_TABLE_NAME`, `MODEL_ARN`, `MODEL_ID`)
- Outputs: `AgentRuntimeArn`, `AgentRuntimeName`

**4. ApiStack** — request path
- Buffered Lambda: `DockerImageFunction` from [lambda/](lambda/), 1024MB/30s, x86_64; env `KB_ID`, `MODEL_ARN`, `MODEL_ID`, `AGENTCORE_RUNTIME_ARN`, `MEMORY_ID`, `CONVERSATIONS_TABLE_NAME`, `DOCS_BUCKET`, `DATA_SOURCE_ID`, `USER_POOL_ID`, `USER_POOL_CLIENT_ID`, `LOG_LEVEL`; IAM scoped to the AgentCore Runtime ARN, Memory ARN, DDB table, KB ARN, S3 `PutObject` on `uploads/*` only, `bedrock-agent:StartIngestionJob`+`GetIngestionJob`
- Streaming Lambda: `DockerImageFunction` from [lambda_stream/](lambda_stream/), 1024MB/60s, x86_64; env adds Cognito IDs + `STREAM_MODEL_ARN`; IAM scoped to `bedrock:InvokeModelWithResponseStream` on the inference profile, Memory, DDB
- `RestApi` (regional, prod stage, `MethodLoggingLevel.INFO`, metrics on); `CognitoUserPoolsAuthorizer` (5-min cache) attached to every non-`/health` route
- Function URL on streaming Lambda: `AuthType=NONE`, `InvokeMode=RESPONSE_STREAM`, CORS = `http://localhost:8501`
- Routes:
  - `GET  /health` (no auth)
  - `POST /query`
  - `GET  /conversations`
  - `GET  /conversations/{session_id}`
  - `POST /documents`
  - `POST /ingest`
  - `GET  /ingest/{job_id}`
- Explicit `LogGroup` (30-day retention) for each Lambda — avoids CDK's `log_retention` custom-resource Lambda which would inflate the function count + break synth-test invariants
- Outputs: `ApiUrl`, `LambdaFunctionName`, `StreamFunctionName`, `StreamFunctionUrl`, `LogGroupName`

## Request flow

**`POST /query` (buffered REST)**

1. APIGW `CognitoUserPoolsAuthorizer` validates the ID JWT (signature, `aud`, `iss`, `exp`). Lambda sees claims at `event.requestContext.authorizer.claims`.
2. Buffered Lambda extracts `actor_id = claims["sub"]`, validates the request body (pydantic), and calls `bedrock-agentcore.invoke_agent_runtime` with payload `{question, session_id, actor_id, top_k}` and `runtimeSessionId` aligned with `session_id` (padded to ≥33 chars if shorter).
3. AgentCore Runtime container runs `agent/rag.py`:
   - `bedrock-agent-runtime.Retrieve(KB_ID, retrievalQuery, vectorSearchConfiguration={numberOfResults: top_k})`
   - Builds prompt: system "answer ONLY from context; if insufficient, respond `INSUFFICIENT_CONTEXT`; cite as [n]". User: numbered chunks + question.
   - `bedrock-runtime.invoke_model(MODEL_ARN, messages, max_tokens=800, temperature=0.1)`
   - If `answer.strip().startswith("INSUFFICIENT_CONTEXT")` → canned `"I don't have information about that in the knowledge base."` + confidence clamped to ≤ 0.2.
   - Confidence: `clamp(0.6·max(scores) + 0.3·mean(scores) + 0.1·(1 − stdev(scores)), 0, 1)`
   - On first turn (`list_events(maxResults=1)` empty): after the assistant answer is available, LLM-generate `conversation_name` from the user question + answer (3–5 words, ≤50 chars) and `PutItem` to DDB.
   - One `CreateEvent` on Memory per turn with native `conversational` payload (USER + ASSISTANT items).
4. Lambda returns the response wrapped in `QueryResponse`.

**`POST /query-stream` (Function URL SSE)**

1. Streaming Lambda parses `Authorization: Bearer …`, validates against Cognito JWKS (`pyjwt[crypto]` + `PyJWKClient`), asserts `token_use == "id"`.
2. `Retrieve` synchronously (same as buffered path).
3. `bedrock-runtime.invoke_model_with_response_stream(...)` — iterate chunks, emit SSE frames: `meta` → `token`×N → `sources` → `done`.
4. After the stream completes: `INSUFFICIENT_CONTEXT` marker handling, confidence calc, then write to AgentCore Memory + DDB (same shape as buffered path). Memory + DDB write logic is **duplicated** between `agent/rag.py` and `lambda_stream/stream_app.py` because Docker contexts are isolated and AgentCore Runtime has no streaming counterpart — this is a deliberate tradeoff, not tech debt.

**`GET /conversations` + `GET /conversations/{session_id}`** ([lambda/conversations.py](lambda/conversations.py))

- List: DDB `Query` on `actor_id`, returns `(session_id, conversation_name, created_at)` per row. Numbers coerced to `int` before `json.dumps` to dodge the `Decimal` trap.
- Replay: paginated `bedrock-agentcore.list_events(actor_id, session_id)` with a full `nextToken` loop (the AWS sample silently truncates here).

**Upload + ingest** ([lambda/uploads.py](lambda/uploads.py))

- `POST /documents` → validates `(filename, content_type)` against a 9-extension whitelist with cross-checked MIME types; sanitizes filename to `[A-Za-z0-9._-]`, builds key `uploads/{yyyy-mm-dd}/{uuid4-hex}-{sanitized}`; `s3.generate_presigned_url("put_object", ..., ExpiresIn=900, signature_version="s3v4")` with the signed `ContentType`.
- Client `PUT` to S3 directly with the same `Content-Type` header (mismatch → 403 `SignatureDoesNotMatch`).
- `POST /ingest` → validates `key` starts with `uploads/`; calls `bedrock-agent.start_ingestion_job` with `clientToken = "rag-{uuid4-hex}"` (36 chars to satisfy the ≥33-char minimum) for idempotent retries.
- `GET /ingest/{job_id}` → `get_ingestion_job` returns status + statistics. Client polls every ~2s up to 90s (Streamlit shows `st.status` progress panel).

## Implementation phases (historical, sub-agent-sized)

Each phase ran as a tight, self-contained brief sent to a `general-purpose` sub-agent, then trust-but-verified by the orchestrator (re-read files, re-run tests, live verification). Per-phase details + files + DoD + live IDs live in [SESSION_HANDOFF.md](SESSION_HANDOFF.md).

| Phase | Goal | Status |
|---|---|---|
| 1 | Scaffolding + sample docs + legacy move | ✅ |
| 2 | CDK StorageStack | ✅ + live-deployed |
| 3 | Lambda handler + container image | ✅ |
| 4 | CDK ApiStack (Lambda + APIGW + API key auth — later replaced) | ✅ + live-deployed |
| 5 | Operator scripts + ingestion + end-to-end RAG | ✅ |
| 6 | Streamlit client + smoke test + eval harness | ✅ |
| 7 | Final README + cleanup + hardening notes | ✅ |
| 8 | DDB chat history + AgentCore Runtime + AgentCore Memory + Cognito JWT (API key removed) | ✅ + live-deployed |
| 9a | Streaming responses via Lambda Function URL + SSE | ✅ + live-deployed |
| 9b | Upload + ingestion REST endpoints (`/documents`, `/ingest`, `/ingest/{id}`) | ✅ + live-deployed |
| 10 | `scripts/run_streamlit.sh` developer-loop polish (auto-syncs `secrets.toml` from live CFN + Secrets Manager) | ✅ |
| 11 | Consistency review + Lambda-backed test-user password sync (closed the `AwsCustomResource` + `unsafe_unwrap()` trap) | ✅ + live-redeployed |
| 12 | Production-like deploy: APIGW method throttling + multi-origin CORS + Cognito callbacks for Streamlit Community Cloud at `rag-aws-kb.streamlit.app` | ✅ + live-deployed |
| 13 | UI redesign (ChatGPT-style sidebar with time-buckets + active highlight, theme.toml auto light/dark, header Settings popover + Upload dialog, Acme removed from UI strings) + per-turn `sources`/`confidence`/`latency_ms`/`model_id` persistence via base64-encoded AgentCore Memory `blob` sidecar so reloaded conversations render the full assistant payload | ✅ + live-redeployed |
| 14 | CDK-native KB pre-seeding (StorageStack `BucketDeployment` + Lambda-backed `SeedKnowledgeBase` CR fires `StartIngestionJob` on every `cdk deploy`; SHA-256 content hash makes unchanged redeploys no-ops; `prune=False` preserves Phase 9 user uploads). Operator scripts retained for manual re-ingests but no longer required for a fresh deploy. | ✅ + live-deployed |

## Sample documents

6 Markdown files under [sample-docs/](sample-docs/), each 3–4 KB, generated under a single brief: realistic enterprise-policy tone, fictional brand **Acme Notes** (collaborative-note SaaS, `acmenotes.example` domain), footer `*Last updated: 2026-05-01*`. Loaded via [scripts/upload_docs.py](scripts/upload_docs.py) (boto3 sync) then [scripts/start_ingestion.py](scripts/start_ingestion.py). Authenticated users can extend the corpus at runtime via `/documents` + `/ingest` (Phase 9b).

## Auth wiring

- CDK creates the User Pool, App Client (with secret), Hosted UI domain, and one pre-created `demo` user.
- The test-user password is generated into Secrets Manager. A small **Lambda-backed Custom Resource** reads the secret via `boto3.client("secretsmanager").get_secret_value(...)` at runtime and calls `AdminSetUserPassword` (Phase 11 hardening fix — the original `AwsCustomResource` + `secret_value_from_json().unsafe_unwrap()` pattern was broken in this context because CFN does not resolve `secretsmanager` dynamic references inside Custom Resource properties, so the literal `{{resolve:...}}` string was being passed verbatim as the password). The CR re-fires on every fresh deploy after `cdk destroy --all` via its ARN-bound `physical_resource_id` (no manual `admin-set-user-password` ritual needed).
- App Client secret copied into a Secrets Manager secret for launcher consumption.
- [scripts/run_streamlit.sh](scripts/run_streamlit.sh) reads `aws cloudformation describe-stacks` (AuthStack + ApiStack) + Secrets Manager → rewrites `streamlit_client/.streamlit/secrets.toml` → `exec`s Streamlit. **The reviewer never edits `secrets.toml` by hand.**
- Streamlit calls `st.login()` → Cognito Hosted UI → callback to `http://localhost:8501/oauth2callback` → ID token on `st.user.tokens["id"]`. Sent as `Authorization: Bearer …` on every backend call.
- REST routes: APIGW Cognito authorizer validates JWT before Lambda runs. Function URL: streaming Lambda validates JWKS in-handler.
- Smoke + eval: [scripts/get_id_token.py](scripts/get_id_token.py) uses `ADMIN_USER_PASSWORD_AUTH` against the same App Client (with `SECRET_HASH` because the client has a secret) to mint a fresh token.

## Reused logic from legacy code

| Legacy file | What to port | Where |
|---|---|---|
| [legacy/rag/generator.py:28-74](legacy/rag/generator.py#L28-L74) | Grounded prompt shape (drop "explain like 10" + related questions) | [agent/rag.py](agent/rag.py) + [lambda_stream/stream_app.py](lambda_stream/stream_app.py) |
| [legacy/rag/generator.py:217-264](legacy/rag/generator.py#L217-L264) | Confidence formula shape (simplified, no keyword boost) | [agent/rag.py](agent/rag.py) + [lambda_stream/stream_app.py](lambda_stream/stream_app.py) |
| [legacy/backend/models/query.py](legacy/backend/models/query.py) | Response schema seed (rebuilt as pydantic v2) | [lambda/schemas.py](lambda/schemas.py) (+ copies in `lambda_stream/` and `agent/`) |

Everything else (FAISS, Gemini, document loaders, Streamlit upload UI, FastAPI routes) is **not** reused — the new architecture replaces it.

## Validation

End-to-end, after a `cdk deploy --all`:

1. `cd infra && env -u PYTHONPATH cdk synth` → all 4 stacks synth cleanly
2. `cdk deploy --all --outputs-file ../cdk-outputs.json` → 4 stacks `CREATE_COMPLETE` or `UPDATE_COMPLETE`
3. `python scripts/upload_docs.py` → 6 docs in S3 (idempotent)
4. `python scripts/start_ingestion.py` → ingestion `COMPLETE` (~10s for 6 docs)
5. `python scripts/smoke_test.py` → exits 0 (7 checks: `/health` 200, `/query` happy path, `/query` missing-token 401, `/query` empty-question 400, `/conversations` 200, `/query-stream` SSE 200, upload+ingest+follow-up-query 90s flow)
6. `python tests/eval/run_eval.py` → writes [tests/eval/eval_results.md](tests/eval/eval_results.md) (8 questions: 6 in-corpus, 1 ambiguous, 1 off-corpus)
7. `./scripts/run_streamlit.sh` → opens Streamlit at `http://localhost:8501`; sign in as `demo` (password in Secrets Manager); ask "What is the refund window?" → grounded answer + `refund-policy.md` in sources + confidence ~0.8
8. Optional: upload a custom Markdown doc via the sidebar, ingest, query — observe new doc in sources
9. `cdk destroy --all` → all resources removed; vector index + Memory deletion handled by CDK; S3 buckets emptied by `autoDeleteObjects=True`

Unit + synth tests: ~76 pytest cases across `tests/test_schemas.py` (pydantic), `test_agent.py` (botocore Stubber + mocked AgentCore), `test_stream.py` (mocked PyJWKClient + Claude stream EventStream), `test_uploads.py` (mocked S3 + bedrock-agent), `test_synth.py` (CDK template assertions).

## Production hardening (README-documented, not built unless noted)

**Implemented** in this submission (see [README §9](README.md)):

- Per-user identity via Cognito JWT; per-user data isolation enforced by `actor_id`-keyed DDB + Memory
- Least-privilege IAM (no wildcards; specific ARNs; `s3:PutObject` scoped to `uploads/*` only)
- Structured JSON logs with per-request `request_id`; 30-day log retention; AgentCore Memory event expiry 30d
- Schema validation at every boundary (pydantic v2); generic 500 error messages with full traceback in CloudWatch only
- Secrets via Secrets Manager (no plaintext in synth output); CDK source is the only allowed change path
- `autoDeleteObjects=True` + `RemovalPolicy.DESTROY` for clean teardown
- Streaming UX + runtime upload + ingestion with progress feedback
- Lambda-backed test-user password sync (Phase 11): a small Custom Resource reads the secret at runtime and calls `AdminSetUserPassword`, replacing the broken `AwsCustomResource` + `unsafe_unwrap()` pattern. ARN-bound `physical_resource_id` re-fires on fresh deploy.

**Documented but not implemented** — production roadmap:

- Per-user rate limits + APIGW WAF (rate, geo, OWASP common rules)
- VPC + interface endpoints (Bedrock, Secrets Manager, S3, DynamoDB); both Lambdas in private subnets
- KMS CMKs on docs bucket, all secrets, all log groups, DDB table, Memory
- X-Ray tracing through APIGW + Lambda + AgentCore Runtime + Bedrock; budget alarms; per-user dashboards
- Bedrock model fallback (Haiku → Sonnet with backoff); SnapStart for container Lambdas when GA; DLQ for failed invocations
- Event-driven background ingestion (S3 PutObject → EventBridge → StartIngestionJob) as a complement to the explicit `/ingest` route — for bulk uploads where polling per file is too chatty
- AgentCore Gateway for sharing tools across multiple agents; AgentCore Identity for per-tool fine-grained auth
- Output PII redaction before logging; input PII scan + reject on `/query`
- Query-rewriting for ambiguous questions; reranking (Bedrock supports it); per-source recall + groundedness eval gate in CI
- Per-user upload isolation (currently shared corpus — flagged in known limitations)
- Consolidate the `agent/rag.py` ↔ `lambda_stream/stream_app.py` duplication if/when AgentCore Runtime gains a streaming counterpart

## Optional extensions — status

From the brief's "Optional extensions" menu:

| Extension | Status | Where |
|---|---|---|
| DynamoDB-backed chat/session history | ✅ delivered Phase 8 | `StorageStack.ConversationsTable` |
| Amazon Bedrock AgentCore Runtime | ✅ delivered Phase 8 | `AgentStack` |
| Amazon Bedrock AgentCore Memory | ✅ delivered Phase 8 | `StorageStack.AgentMemory` |
| Streaming responses from the API to Streamlit | ✅ delivered Phase 9a | `lambda_stream/` + Function URL SSE |
| Upload or ingestion endpoint for new documents | ✅ delivered Phase 9b | `lambda/uploads.py` + 3 REST routes |
| CI/CD pipeline for CDK deployment | ⏳ not delivered | candidate for a future phase |
| Guardrails or safety filters | ⏳ not delivered | candidate for a future phase (Bedrock Guardrails) |
| Human feedback collection | ⏳ not delivered | candidate for a future phase |
| Cost controls and token usage tracking | ⏳ not delivered | candidate for a future phase |

Plus one bonus: Cognito User Pool + JWT auth (replaced the API-key path entirely in Phase 8).

## Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | S3 Vectors regional availability (us-east-1, us-west-2, eu-west-1, ap-southeast-2 at GA) | Region pinned in [infra/app.py](infra/app.py); README documents the requirement |
| 2 | Bedrock model access must be enabled manually in account | README Step 0; smoke test maps `AccessDeniedException` to an actionable hint |
| 3 | Lambda cold start ~2s (RIC) / ~4–6s (LWA first call) | Module-scope clients; 1024 MB; warm calls land ~1–1.5s; no provisioned concurrency (cost) |
| 4 | KB ingestion is async (~10s for 6 docs, longer for larger) | `start_ingestion.py` polls every 10s, 5-min timeout, prints status |
| 5 | AgentCore Runtime is arm64-only | `DockerImageAsset` pinned to `LINUX_ARM64`; cross-build via Buildx + QEMU on x86 hosts |
| 6 | Cognito Hosted UI prefix can't contain `aws`/`amazon`/`cognito` | Uses `ragkb-{account-suffix}` |
| 7 | DDB `Decimal` ↔ `json.dumps` | Explicit `int()` coercion at the boundary in [lambda/conversations.py](lambda/conversations.py) |
| 8 | Presigned PUT requires the client to echo the signed `Content-Type` | Streamlit sends `Content-Type` explicitly; smoke test covers the contract |
| 9 | `cdk destroy --all` rotates every ID | `scripts/run_streamlit.sh` reads live values from CFN + Secrets Manager on every launch (local dev); `scripts/print_streamlit_cloud_secrets.py` prints a paste-ready TOML for the Streamlit Cloud Secrets editor (prod) |
| 10 | $20 budget runaway | API-key UsagePlan removed in Phase 8; Phase 12 added per-method APIGW throttling (rate=10/burst=20 on most routes, 5/10 on `/ingest`) as the replacement. Cumulative spend across 12 phases under $3 of $20. |
| 11 | Streamlit Cloud secrets are dashboard-managed (no public API) | After each `cdk destroy --all` + redeploy, re-run `scripts/print_streamlit_cloud_secrets.py | pbcopy` and paste into Streamlit Cloud Secrets editor. Cookie secret persisted locally to keep sessions warm across re-pastes. |
