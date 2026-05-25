# RAG-AWS — Knowledge Base Agent on AWS

> **Status: live and reviewer-ready.** A local Streamlit client signs in with Cognito JWT and calls a CDK-deployed AWS API that fronts a Bedrock Knowledge Base (S3 Vectors) and Anthropic Claude Haiku 4.5, with **per-session conversation memory** (AgentCore Memory + DynamoDB), **streaming responses** (Lambda Function URL + SSE), and **runtime document upload + ingestion**. Smoke test passes 7/7; live multi-turn validation confirms DDB + AgentCore Memory state both stay in sync with the user's chat.

This repository is a productionization of a Streamlit + Gemini + FAISS prototype (preserved under [legacy/](legacy/)). It satisfies the AWS-Native Knowledge Base Agent take-home brief and lands six of the optional extensions (Cognito auth, AgentCore Runtime, AgentCore Memory, DynamoDB chat history, streaming responses, upload + ingestion endpoint).

---

## 1. Architecture

```
┌─────────────────────────── LOCAL  (reviewer's machine) ───────────────────────────┐
│                                                                                   │
│   ┌──────────────────────┐   secrets.toml:                                        │
│   │  Streamlit chat UI   │     [api]    api_base_url = https://…/prod/           │
│   │  streamlit_client/   │     [stream] stream_url   = https://…lambda-url.aws/  │
│   │   • st.login(Cognito)│   At runtime: st.user.tokens["id"] = Cognito ID JWT    │
│   │   • Stream toggle    │   Sent on every call as Authorization: Bearer <jwt>    │
│   │   • File uploader    │                                                        │
│   │   • Sidebar history  │                                                        │
│   └─────────┬────────────┘                                                        │
│             │                                                                     │
│   (1) POST /query          ─► API Gateway REST   (buffered, ≤30s)                 │
│   (1') POST /query-stream  ─► Lambda Function URL (SSE, streaming)                │
│   (1") POST /documents     ─► API Gateway REST   (mint presigned PUT URL)         │
│   (1"') PUT  to S3 directly with presigned URL  (client-to-S3, no Lambda hop)     │
│   (1"") POST /ingest + poll GET /ingest/{job_id}                                  │
└──────────┬──────────────────────────────────────────────────────────────────────┬─┘
           │ HTTPS (TLS 1.2+)                                                     │
           │                                                                      │
┌──────────┼─── AWS  (us-east-1, account 954863244564) ────────────────────────────┐
│          ▼                                                                       │
│   ┌────────────────────────────────────────────────────────────────────────────┐ │
│   │  Cognito User Pool   (JWT issuer)                                          │ │
│   │   • Hosted UI at https://ragkb-244564.auth.us-east-1.amazoncognito.com    │ │
│   │   • One pre-created test user `demo`                                       │ │
│   │   • App Client has a secret (SECRET_HASH on admin-initiate-auth)           │ │
│   └────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│   ┌──────────── REST API path ─────────────┐   ┌──── Function URL path ────────┐ │
│   │ API Gateway (regional, prod stage)     │   │ Lambda Function URL          │ │
│   │   CognitoUserPoolsAuthorizer on every  │   │   AuthType=NONE              │ │
│   │   non-/health route, 5-min cache       │   │   InvokeMode=RESPONSE_STREAM │ │
│   │                                        │   │   CORS: localhost:8501        │ │
│   │   /health    GET    (no auth)          │   │   /query-stream  POST (SSE)  │ │
│   │   /query     POST                      │   │                              │ │
│   │   /conversations              GET      │   │   In-handler JWT verify       │ │
│   │   /conversations/{session_id} GET      │   │   (Cognito JWKS, pyjwt)       │ │
│   │   /documents POST   (mint presigned)   │   └────────────┬─────────────────┘ │
│   │   /ingest    POST   (StartIngestion)   │                │                   │
│   │   /ingest/{job_id} GET (poll)          │                │                   │
│   └────────────────┬───────────────────────┘                │                   │
│                    │                                        │                   │
│                    ▼                                        ▼                   │
│   ┌──────────────────────────────────┐     ┌──────────────────────────────────┐ │
│   │ Buffered Lambda                  │     │ Streaming Lambda                 │ │
│   │  lambda/   (x86_64, RIC image)   │     │  lambda_stream/  (x86_64, LWA +  │ │
│   │   • app.py routes 7 endpoints    │     │   uvicorn ASGI image)            │ │
│   │   • Proxy /query → AgentCore     │     │  • verifies Cognito ID token    │ │
│   │     Runtime via boto3            │     │    against User Pool JWKS        │ │
│   │   • /conversations* reads        │     │  • bedrock-runtime                │ │
│   │     Memory + DDB                 │     │    .InvokeModelWithResponseStream │ │
│   │   • /documents mints presigned   │     │  • emits SSE: meta → token → … │ │
│   │     PUT URL (s3v4, 15-min TTL)   │     │    → sources → done              │ │
│   │   • /ingest calls bedrock-agent  │     │  • on stream-end writes DDB +    │ │
│   │     .StartIngestionJob           │     │    AgentCore Memory event        │ │
│   └─────────────┬────────────────────┘     └─────────────────┬────────────────┘ │
│                 │                                            │                   │
│                 │   InvokeAgentRuntime                       │                   │
│                 ▼                                            │                   │
│   ┌──────────────────────────────────────────────┐           │                   │
│   │ Bedrock AgentCore Runtime                    │           │                   │
│   │  arm64 container from agent/                 │           │                   │
│   │   BedrockAgentCoreApp (no LangGraph)         │           │                   │
│   │   • Retrieve (KB)                            │           │                   │
│   │   • InvokeModel (Claude Haiku 4.5)           │           │                   │
│   │   • CreateEvent on Memory                    │           │                   │
│   │   • PutItem on DDB (first turn only)         │           │                   │
│   └────────────┬─────────────────────────────────┘           │                   │
│                │                                             │                   │
│   ┌────────────┴────────┐   ┌────────────────────┐           │                   │
│   ▼                     ▼   ▼                    ▼           │                   │
│ Bedrock KB        Bedrock     AgentCore       DynamoDB       │                   │
│ Retrieve          Runtime     Memory          ConvTable      │                   │
│                   (Haiku 4.5  ─ Events: native conversational                    │
│                    via US     ─ EventExpiry=30d              │                   │
│                    inf prof)  ─ One event/turn = USER+ASSIST │                   │
│       │              │              │                        │                   │
│       │              │              └────────── replayed by ─┴──────► /convers… │
│       │              │                                                           │
│       │              └─────── invoked directly for streaming ───────────────────┘│
│       │                                                                          │
│       ▼                                                                          │
│  ┌──────────────────────┐                                                        │
│  │  S3 Vectors          │  ◄──────  KB ingestion (on /ingest POST):              │
│  │   dim=1024 cosine    │           bedrock-agent.StartIngestionJob              │
│  │   chunk: FIXED 300t  │           scans /uploads/* under docs bucket,          │
│  │   embed: Titan v2    │           embeds each chunk with Titan v2,             │
│  └──────────────────────┘           writes vectors → S3 Vectors index            │
│                                                                                  │
│  ┌──────────────────────┐                                                        │
│  │  S3 docs bucket      │  ◄── presigned PUT (client direct, no Lambda hop)      │
│  │   sample-docs/*.md   │      uploads/{yyyy-mm-dd}/{uuid4}-{name}.{ext}         │
│  │   uploads/…          │                                                        │
│  └──────────────────────┘                                                        │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Request-flow narrative

A single user turn:

1. **Sign-in** — Streamlit calls `st.login()`; Cognito Hosted UI redirects back with an ID token in `st.user.tokens["id"]`. The token is sent as `Authorization: Bearer …` on every backend call.
2. **Stream toggle ON** (default): Streamlit POSTs `/query-stream` to the Lambda Function URL with the JWT and `{question, session_id, top_k}`. The streaming Lambda verifies the JWT against the Cognito JWKS, runs Retrieve, opens a Bedrock streaming completion, and emits SSE frames: `meta` (request_id, session_id, is_first_turn) → `token` ×N → `sources` → `done` (latency_ms, model_id, confidence, conversation_name). On stream end, the Lambda writes one event to AgentCore Memory (USER + ASSISTANT payload, `conversational` shape) and, on first turn, one row to DynamoDB (conversation metadata).
3. **Stream toggle OFF**: Streamlit POSTs `/query` to the REST API. APIGW validates the JWT via its `CognitoUserPoolsAuthorizer`. The buffered Lambda proxies to AgentCore Runtime via `InvokeAgentRuntime`. The runtime container (arm64) runs the same Retrieve + InvokeModel + Memory/DDB writes inside `BedrockAgentCoreApp` and returns the full grounded response synchronously.
4. **Sidebar history** — Streamlit calls `GET /conversations` (DDB Query on `actor_id`) to list past sessions, and `GET /conversations/{session_id}` (paginated AgentCore Memory `ListEvents`) to replay one session's turns.
5. **Document upload** — user picks a file in the sidebar; Streamlit POSTs `/documents` to mint a presigned S3 PUT URL, PUTs the bytes directly to S3, then POSTs `/ingest` to start a Bedrock KB ingestion job, then polls `GET /ingest/{job_id}` every 2s. On `COMPLETE`, the new doc is immediately queryable via both `/query` and `/query-stream`.

### 1.2 Key tradeoffs

| Decision | Chose | Over | Why |
|---|---|---|---|
| Vector store | **S3 Vectors** (native to Bedrock KB) | OpenSearch Serverless | Brief forbids OSS; S3 Vectors is the only GA, low-idle-cost AWS-managed vector option (GA Jan 2026). |
| LLM | **Claude Haiku 4.5** via cross-region inference profile | Sonnet; Claude 3 Haiku | Brief recommended Claude 3 Haiku, but it became LEGACY post-2026 and Marketplace-gated. Haiku 4.5 is the modern equivalent; inference profile gives multi-region failover for free. |
| Embeddings | **Titan v2 (1024-dim)** | Titan v1; Cohere | Required by S3 Vectors + KB; 1024 dims is the smallest Titan v2 option and keeps index storage cheap. |
| Auth | **Cognito User Pool + JWT** (Phase 8) | API key + UsagePlan; Lambda authorizer | Per-user identity needed for `actor_id` (Memory + DDB primary key). API key path was removed entirely. |
| Conversation state | **AgentCore Memory + DDB metadata** (Phase 8) | Single store | AgentCore Memory holds the *messages* (native `conversational` shape, console-introspectable); DDB holds *metadata* (conversation_name, created_at) for fast sidebar listing without scanning Memory. |
| Agent runtime | **AgentCore Runtime + arm64 container** (Phase 8) | LangGraph; pure Lambda | Adds runtime memory + managed scaling + console observability for the agent. Hybrid: REST `/query` is a thin Lambda proxy that calls `InvokeAgentRuntime`; the runtime container does the RAG. No LangGraph framework weight — pure boto3 inside `BedrockAgentCoreApp`. |
| Streaming transport | **Lambda Function URL + SSE** (Phase 9a) | API Gateway WebSocket; HTTP API v2; direct AgentCore-from-client | REST API can't stream; Function URL + LWA + uvicorn ASGI is the only AWS-native path for Python Lambda response streaming. Lives alongside `/query` so the deterministic path stays available for smoke + eval. |
| Streaming source | **bedrock-runtime.InvokeModelWithResponseStream** directly (Phase 9a) | AgentCore Runtime streaming | AgentCore Runtime's `InvokeAgentRuntime` API is buffered-only as of May 2026. The streaming Lambda goes straight to Bedrock and writes Memory/DDB itself after the stream completes — keeps behavior byte-identical to `/query`. |
| Upload mechanism | **Presigned S3 PUT URL** (Phase 9b) | Multipart-through-Lambda | No Lambda payload limit (Lambda body cap is 6MB sync / 10MB stream), no Lambda compute cost per byte, AWS-native pattern. Client PUTs directly to S3. |
| Ingestion trigger | **Explicit POST /ingest + polled status** (Phase 9b) | S3 event auto-trigger | Gives the user feedback (progress bar) and explicit control over when ingestion cost is incurred. Shared corpus — all authenticated users see all uploaded docs. |
| RAG strategy | **`Retrieve` + `InvokeModel` separately** | `RetrieveAndGenerate` | Gives full prompt control (the INSUFFICIENT_CONTEXT contract requires it) and a clean schema for `sources[]`. |
| Chunking | **FIXED_SIZE 300 tokens, 20% overlap** | Semantic chunking; hierarchical | Sample docs are short policy markdown; fixed chunking is predictable, debuggable, and adequate. |
| IaC | **AWS CDK in Python, 4 stacks** | Terraform; SAM; single stack | Brief mandate. Four stacks (Storage / Auth / Agent / Api) so each can be redeployed independently — and so the slow arm64 image build only re-runs when the agent code changes. |

---

## 2. AWS services + CDK stacks

| Service | Role | CDK construct |
|---|---|---|
| **Cognito User Pool + App Client** | Issues ID JWTs; Hosted UI for sign-in; one pre-created test user. App Client has a secret. | `aws_cognito.UserPool` + `UserPoolClient` + `UserPoolDomain` |
| **API Gateway** (REST, regional) | Public HTTPS entry point. `CognitoUserPoolsAuthorizer` on every non-`/health` route. Seven routes total. | `aws_apigateway.RestApi` |
| **Lambda — buffered** (container, x86_64) | Routes `/query` (proxy to AgentCore Runtime), `/conversations*` (Memory + DDB), `/documents` (presigned URL), `/ingest`, `/ingest/{job_id}`. 1024MB / 30s. | `aws_lambda.DockerImageFunction` from `lambda/` |
| **Lambda — streaming** (container, x86_64) | Hosts FastAPI/uvicorn behind AWS Lambda Web Adapter. Exposes `/query-stream` via Function URL with `InvokeMode=RESPONSE_STREAM`. JWT verified in-handler against Cognito JWKS. 1024MB / 60s. | `aws_lambda.DockerImageFunction` + `add_function_url` from `lambda_stream/` |
| **AgentCore Runtime** (arm64) | Hosts the agent code (`agent/`). `BedrockAgentCoreApp` wraps Retrieve + InvokeModel + Memory + DDB writes. Invoked via `bedrock-agentcore.InvokeAgentRuntime` from the buffered Lambda. | `aws_bedrockagentcore.CfnRuntime` (CFN L1) |
| **AgentCore Memory** | Stores conversational events (one per turn, USER+ASSISTANT). `EventExpiryDuration=30 days`. No semantic strategies. | `aws_bedrockagentcore.CfnMemory` |
| **DynamoDB** (`ConversationsTable`) | PK `actor_id` (JWT sub), SK `session_id`; attrs `conversation_name`, `created_at`. PAY_PER_REQUEST. | `aws_dynamodb.Table` |
| **Bedrock Knowledge Base** | Chunks + embeds docs; serves `Retrieve`. FIXED_SIZE 300 / 20%. | `aws_bedrock.CfnKnowledgeBase` |
| **Bedrock Runtime** (Haiku 4.5) | Buffered + streaming inference. Cross-region US profile. | (no resource — IAM-granted access) |
| **S3** docs bucket | Source docs + uploads. `autoDeleteObjects=True`. BucketOwnerEnforced. | `aws_s3.Bucket` |
| **S3 Vectors** | Native vector store backing the KB. dim=1024, cosine, float32. | `aws_s3vectors.CfnVectorBucket` + `CfnIndex` |
| **Secrets Manager** | App Client secret + test-user password (both auto-generated). | `aws_secretsmanager.Secret` |
| **CloudWatch Logs** | Structured JSON for both Lambdas + APIGW access logs. 30-day retention. | `aws_logs.LogGroup` |
| **IAM** (least-privilege) | Per-Lambda inline policies. Buffered Lambda has IAM scoped to the AgentCore Runtime ARN, Memory ARN, DDB table, KB ARN (for ingestion), and `s3:PutObject` on `uploads/*` only. Streaming Lambda has IAM scoped to the streaming inference profile + Memory + DDB. | inline policies |

**Four CDK stacks** ([infra/app.py](infra/app.py)) — they depend in this order; cross-stack refs are in-memory:

1. **StorageStack** ([infra/stacks/storage_stack.py](infra/stacks/storage_stack.py)) — docs bucket, S3 Vectors bucket+index, Bedrock KB + data source, KB IAM role, DDB ConversationsTable, AgentCore Memory.
2. **AuthStack** ([infra/stacks/auth_stack.py](infra/stacks/auth_stack.py)) — Cognito User Pool, App Client (with secret), Hosted UI domain (`ragkb-{account-suffix}` — see [§7 Gotchas](#7-gotchas-from-the-live-deploy)), one pre-created test user `demo` with a Secrets Manager-managed password.
3. **AgentStack** ([infra/stacks/agent_stack.py](infra/stacks/agent_stack.py)) — AgentCore Runtime, arm64 `DockerImageAsset` built from `agent/`, runtime IAM role.
4. **ApiStack** ([infra/stacks/api_stack.py](infra/stacks/api_stack.py)) — buffered Lambda + streaming Lambda + REST API with Cognito authorizer + Function URL + 7 routes + all IAM.

Outputs are written to [cdk-outputs.json](cdk-outputs.json) (gitignored) and consumed by all operator scripts.

---

## 3. API contract & authentication

### 3.1 Endpoints

| Method | Path | Transport | Auth | Purpose |
|---|---|---|---|---|
| `GET`  | `/health` | REST | none | Liveness probe; no AWS calls. |
| `POST` | `/query`  | REST | Cognito JWT | Buffered grounded RAG answer (≤30s). |
| `POST` | `/query-stream` | Function URL (SSE) | Cognito JWT (in-handler verify) | Streaming grounded RAG answer. |
| `GET`  | `/conversations` | REST | Cognito JWT | List past sessions for the calling user. |
| `GET`  | `/conversations/{session_id}` | REST | Cognito JWT | Replay one session's turns (Memory events). |
| `POST` | `/documents` | REST | Cognito JWT | Mint a presigned S3 PUT URL for one upload. |
| `POST` | `/ingest` | REST | Cognito JWT | Start a Bedrock KB ingestion job. |
| `GET`  | `/ingest/{job_id}` | REST | Cognito JWT | Poll an ingestion job's status. |

### 3.2 `/query` request

```json
{ "question": "What is the refund window for monthly plans?", "session_id": "s-abc123", "top_k": 5 }
```
- `question` (string, required, min length 1).
- `session_id` (string, optional) — if omitted, the API generates one. Used as the AgentCore Memory `sessionId` + DDB sort key. Passing the same `session_id` across calls accumulates turns under one conversation.
- `top_k` (int, optional, default 5, range 1–20).

### 3.3 `/query` 200 response

```json
{
  "answer": "According to the Acme Notes Refund Policy, the refund window for monthly plans is **30 days** of the most recent charge … [1]",
  "confidence": 0.854,
  "sources": [
    { "s3_uri": "s3://…/refund-policy.md", "document": "refund-policy.md", "score": 0.872, "snippet": "…" },
    …
  ],
  "metadata": {
    "model":              "anthropic.claude-haiku-4-5-20251001-v1:0",
    "retrieval_strategy": "bedrock-kb-s3vectors-titan-v2-topk",
    "request_id":         "10cef5e1-…",
    "latency_ms":         4640
  },
  "session_id":         "s-abc123",
  "conversation_name":  "Acme Notes Monthly Refund Policy"
}
```
- `conversation_name` is generated by an LLM call on the first turn of a session (3–5 words, ≤50 chars) and persisted to DDB. On turns 2+, it is `null` and the existing DDB row is not overwritten.

### 3.4 `/query-stream` wire format (Server-Sent Events)

```
event: meta
data: {"request_id":"…","session_id":"…","is_first_turn":true}

event: token
data: {"text":"According"}

event: token
data: {"text":" to"}
… (many)

event: sources
data: {"sources":[{...},{...},...]}

event: done
data: {"latency_ms":2497,"model_id":"anthropic.claude-haiku-4-5-20251001-v1:0","confidence":0.672,"conversation_name":"Monthly Plan Refund Policy"}
```

On error, the stream ends with a single `event: error` frame. Auth failures return JSON (HTTP 401), not SSE — the client should check status before opening the event loop.

If the model emits `INSUFFICIENT_CONTEXT`, the streaming Lambda appends `"\n\n" + "I don't have information about that in the knowledge base."` as a final `token` frame and clamps `confidence` to 0.2 in `done`. The Streamlit client detects the marker prefix and rewrites the visible text to just the canned answer (see [streamlit_client/app.py:230-238](streamlit_client/app.py#L230-L238)).

### 3.5 Upload + ingestion request shapes

```http
POST /documents
{"filename": "engineering-roadmap.md", "content_type": "text/markdown"}
→ 200 {"upload_url": "https://…", "key": "uploads/2026-05-25/abc-engineering-roadmap.md", "content_type": "…", "expires_in": 900}

PUT  <upload_url>           (client → S3 directly, must echo same Content-Type)

POST /ingest
{"key": "uploads/2026-05-25/abc-engineering-roadmap.md", "client_token": null}
→ 200 {"job_id": "26EXBYYLCZ", "status": "STARTING"}

GET  /ingest/26EXBYYLCZ
→ 200 {"job_id": "…", "status": "COMPLETE", "statistics": {"scanned": 8, "indexed": 1, "failed": 0, "deleted": 0, "modified": 0}, "failure_reasons": null}
```

Allowed extensions and required MIME types are enforced server-side (cross-checked at `/documents`): `.txt text/plain`, `.md text/markdown`, `.html`/`.htm text/html`, `.csv text/csv`, `.pdf application/pdf`, `.doc application/msword`, `.docx application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `.xls application/vnd.ms-excel`, `.xlsx application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`. Soft client cap of 50 MB.

The S3 key is sanitized to `[A-Za-z0-9._-]` and prefixed with `uploads/{yyyy-mm-dd}/{uuid4-hex}-` to avoid collisions across users.

### 3.6 Error envelope

```json
{ "error": "InvalidRequest", "message": "…detail…", "request_id": "…" }
```

| Status | `error`           | When                                                                 |
|------:|-------------------|----------------------------------------------------------------------|
| 400   | `InvalidRequest`  | Bad JSON; missing/empty `question`; `top_k` out of range; bad MIME; key not under `uploads/`. |
| 401   | (Cognito)         | Missing/expired/invalid JWT. APIGW authorizer rejects before Lambda runs. Function URL rejects in-handler. |
| 404   | `InvalidRequest`  | Ingestion job_id not found. |
| 409   | `InvalidRequest`  | `client_token` already used for a different ingestion. |
| 500   | `Internal`        | Bedrock `ClientError` or unhandled exception. Generic message; full traceback in CloudWatch keyed by `request_id`. |

### 3.7 Authentication flow

- **How the token is created**: the Cognito User Pool issues an ID JWT signed with RS256. `st.login()` runs the OAuth Code flow against the Hosted UI; tokens land on `st.user.tokens["id"]`.
- **How it's verified**: 
  - REST API routes — APIGW's `CognitoUserPoolsAuthorizer` validates signature, `aud`, `iss`, `exp` before invoking Lambda. Claims are exposed to the Lambda at `event.requestContext.authorizer.claims`.
  - Function URL `/query-stream` — the streaming Lambda parses `Authorization: Bearer …` and validates against the User Pool JWKS (`pyjwt[crypto]` + `PyJWKClient`). Explicitly checks `token_use == "id"` so an access-token can't be substituted.
- **Per-user identity**: the `sub` claim becomes the `actor_id` used as the DDB primary key + AgentCore Memory `actorId`. No two users can see each other's sessions.
- **Pre-created test user**: AuthStack creates `demo` with a password generated into Secrets Manager. The smoke + eval scripts fetch the password + App Client secret, then call `admin-initiate-auth` with `SECRET_HASH` to get a fresh ID token.

---

## 4. RAG behavior

### 4.1 Ingestion

**Bootstrap** (operator-driven, one-time per deploy):
1. `scripts/upload_docs.py` syncs `sample-docs/*.md` to S3 (MD5-idempotent).
2. `scripts/start_ingestion.py` calls `bedrock-agent.StartIngestionJob` and polls every 10s. KB chunks each doc using **FIXED_SIZE 300 tokens with 20% overlap**, embeds with **Titan v2 (1024-dim)**, writes vectors to S3 Vectors. Typical wall-clock: ~10s for 6 docs.

**Runtime** (`/documents` + `/ingest` + `/ingest/{job_id}`):
- Same `StartIngestionJob` API as bootstrap, but triggered per-upload by an authenticated user via the REST routes (Phase 9b). `client_token` is auto-generated (`rag-{uuid4-hex}`, 36 chars to satisfy the 33-char minimum) for idempotent retries.

### 4.2 Retrieval

`bedrock-agent-runtime.Retrieve(knowledgeBaseId, retrievalQuery, vectorSearchConfiguration={numberOfResults: top_k})`. Returns `[{content, score, location.s3Location.uri}, …]`. Filename is extracted into the `document` field.

### 4.3 Prompt construction & grounding

System prompt (verbatim, lives in [agent/rag.py](agent/rag.py) and [lambda_stream/stream_app.py](lambda_stream/stream_app.py)):

> Answer ONLY from the provided context. If the context is insufficient to answer the question, your entire response MUST be the single token `INSUFFICIENT_CONTEXT` with no other characters, words, or explanation. When you can answer, cite sources inline as [n].

User message: numbered chunks (`[1] …  [2] …`) followed by `Question: <user question>`. The model is invoked via Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) — the US cross-region inference profile fans across us-east-1/us-east-2/us-west-2.

### 4.4 Confidence

```
confidence = clamp(0.6·max(scores) + 0.3·mean(scores) + 0.1·(1 − stdev(scores)), 0, 1)
```

Top-match score dominates (60%); mean rewards consistent matching (30%); stdev penalty prefers tight clusters over a single outlier hit (10%). Simplified port of the legacy formula with FAISS-specific keyword/length adjustments removed.

### 4.5 INSUFFICIENT_ANSWER override

If the model's answer **starts with** `INSUFFICIENT_CONTEXT` (Haiku 4.5 sometimes elaborates after the token — discovered in Phase 5), the response is rewritten to the canned `"I don't have information about that in the knowledge base."` and confidence is clamped to ≤ 0.2. Streaming path: the marker tokens flow to the client; the streaming Lambda then appends the canned answer + clamps confidence in `done`; the Streamlit consumer strips the prefix on display.

### 4.6 Per-session memory (Phase 8)

Every turn writes **one event** to AgentCore Memory under `(actor_id, session_id)`, with a native `conversational` payload containing two items:

```json
[
  {"conversational": {"content": {"text": "<user question>"},      "role": "USER"}},
  {"conversational": {"content": {"text": "<assistant answer>"},    "role": "ASSISTANT"}}
]
```

`is_first_turn` is detected by `list_events(maxResults=1)`. On first turn only, the agent also writes a single DDB row with `conversation_name` (LLM-generated 3–5 words) + `created_at` (Unix epoch int). The sidebar reads from DDB (fast `Query`); replay reads from Memory (paginated `ListEvents`). This is the same shape used by both `/query` and `/query-stream`.

---

## 5. Sample documents

The KB is seeded with 6 Markdown documents under [sample-docs/](sample-docs/), each 3–4 KB, all under a fictional brand **Acme Notes** (collaborative-note SaaS, domain `acmenotes.example`):

| File | Topic |
|---|---|
| [refund-policy.md](sample-docs/refund-policy.md) | Refund windows, eligibility, exclusions. |
| [shipping-policy.md](sample-docs/shipping-policy.md) | Notebook shipping rates + timing. |
| [security-policy.md](sample-docs/security-policy.md) | Authentication, encryption, incident response. |
| [employee-handbook.md](sample-docs/employee-handbook.md) | PTO, benefits, work expectations. |
| [product-faq.md](sample-docs/product-faq.md) | Plan comparison, feature availability. |
| [acceptable-use.md](sample-docs/acceptable-use.md) | Prohibited content + behavior. |

Generated by Claude under a brief: realistic enterprise-policy tone, no PII, no real-brand collisions, footer `*Last updated: 2026-05-01*`. The corpus is intentionally small and topically diverse so retrieval behavior is easy to reason about during evaluation.

Authenticated users can add new documents at runtime via the `/documents` → `/ingest` flow described in [§3.5](#35-upload--ingestion-request-shapes).

---

## 6. Demo evidence — live verification (2026-05-25)

### 6.1 Multi-turn session with DDB + Memory inspection

A single session (`session_id = test-multi-turn-…`) ran three turns and was inspected directly:

| Turn | Question | Confidence | conversation_name | Top sources |
|---|---|---:|---|---|
| 1 | "What is the refund window for monthly plans at Acme Notes?" | 0.854 | **"Acme Notes Monthly Refund Policy"** | refund-policy.md ×3 |
| 2 | "And what about for annual plans?" | 0.601 | `null` (not first turn) | product-faq.md + refund-policy.md |
| 3 | "What is the office Wi-Fi password?" | **0.200** (clamped) | `null` | (canned INSUFFICIENT answer) |

Direct storage inspection after turn 3:
- **DDB ConversationsTable**: exactly **1 row** for this session, `conversation_name="Acme Notes Monthly Refund Policy"`, `created_at` as int. ✓
- **AgentCore Memory**: exactly **3 events**, each with `USER + ASSISTANT` payload items in the native `conversational` shape (verified via `ListEvents`, no msgpack blobs). ✓

### 6.2 Streaming validation

Two streaming sessions captured live:

| Question | First-token | Total | Confidence | conversation_name |
|---|---:|---:|---:|---|
| "What is the refund window for monthly plans?" | 1329 ms | 2797 ms | 0.672 | "Monthly Plan Refund Policy" |
| "How do I report a security incident?" | 1164 ms | 3904 ms | 0.693 | "Report a Security Incident" |
| "What is the office WiFi password?" | 1287 ms | 2205 ms | 0.200 | (canned) |

SSE wire shape (verbatim, one frame per line is the wire reality):
```
event: meta
data: {"request_id":"10cef5e1-…","session_id":"s-…","is_first_turn":true}
event: token
data: {"text":"According"}
…
event: sources
data: {"sources":[{...}]}
event: done
data: {"latency_ms":2495,"model_id":"…","confidence":0.672,"conversation_name":"Monthly Plan Refund Policy"}
```

### 6.3 Upload + ingestion end-to-end

A custom fictional `engineering-roadmap-XXXXXX.md` (~700 B) was uploaded, ingested, and queried back:
- `POST /documents` → 200 (presigned URL, 900s TTL, 1678-char URL).
- `PUT` to S3 → 200, 0-byte response body.
- `POST /ingest` → 200, `job_id` returned; ingestion reached `COMPLETE` in ~6s with `scanned=8, indexed=1, failed=0` (correct delta behavior).
- 3 follow-up `/query` calls against unique factoids from the new doc — all 3 cited the new doc as the top source.
- One of those queries asked for a (synthetic) credential phrase contained in the doc; **Claude refused to surface it**, demonstrating built-in safety behavior even when grounded context contains the literal answer.

### 6.4 Smoke + eval

- `scripts/smoke_test.py` → **7/7 PASS** (health, query happy, query no-token 401, query empty 400, conversations list 200, query-stream SSE 200, upload+ingestion 90s flow).
- `tests/eval/run_eval.py` (Phase 6 baseline) → 100% source-match on 6 in-corpus questions, mean confidence 0.813, off-corpus clamped to 0.200 (unchanged through AgentCore Runtime introduction in Phase 8).

### 6.5 Operator visibility

`/aws/lambda/ApiStack-RagHandler` + `/aws/lambda/ApiStack-StreamHandler` emit structured JSON per turn, keyed by `request_id`. AgentCore Memory events are visible in the Bedrock console under the Memory resource. DDB items can be inspected via `aws dynamodb scan` or the console.

---

## 7. Gotchas from the live deploy

Four AWS-side gotchas have hit this project across phases and are codified in the handoff doc + memory. They will hit again on a fresh redeploy if not handled:

1. **AgentCore Runtime is arm64-only.** `DockerImageAsset` must build `linux/arm64`. From an x86 Mac the build cross-compiles via Docker Buildx + QEMU (slower first build, works thereafter).
2. **Cognito Hosted UI domain prefixes cannot contain `aws`, `amazon`, or `cognito`.** The project uses `ragkb-{account-suffix}`.
3. **DynamoDB returns `Decimal` for numbers.** Coerce to `int`/`float` before `json.dumps` (see [lambda/conversations.py:38](lambda/conversations.py#L38)).
4. **`AwsCustomResource` + `SecretValue.unsafe_unwrap()` does NOT re-fire on secret rotation.** On a fresh deploy, the test-user password is regenerated but the AdminSetUserPassword custom resource doesn't run with the new value, causing `NotAuthorizedException` on first auth. **One-time workaround**: `aws cognito-idp admin-set-user-password --user-pool-id $UPID --username demo --password "$PWD" --permanent`. A CDK-side fix (binding `physical_resource_id` to a hash of the secret) is on the hardening list. The smoke + eval scripts will surface this on the very first call after deploy.

5. **Python Lambda has no native response streaming** — only Node.js does (as of May 2026). The streaming Lambda ships AWS Lambda Web Adapter (LWA) + FastAPI/uvicorn inside the container; the Function URL is configured with `InvokeMode=RESPONSE_STREAM` and `AWS_LWA_INVOKE_MODE=response_stream`. This is the only supported pattern.
6. **Presigned PUT requires the client to echo the exact `Content-Type` it was signed with**, or S3 returns 403 `SignatureDoesNotMatch`. The presigned URL is also SigV4-signed (`signature_version="s3v4"`) explicitly so the legacy SigV2 fallback can't break the contract.
7. **Every `cdk destroy --all` + redeploy rotates the App Client ID, User Pool ID, App Client secret, API URL, and Function URL.** Hand-maintained `streamlit_client/.streamlit/secrets.toml` will be stale and the login button surfaces a generic "authentication error" (Cognito 404s on the dead user pool's `/oauth2/authorize`). Always launch via `./scripts/run_streamlit.sh`, which reads live values from `aws cloudformation describe-stacks` + Secrets Manager on every run.

---

## 8. AI tools used during development

Built using **Claude Code** (Anthropic's CLI) running Opus 4.7 as the orchestrating agent, with `general-purpose` sub-agents handling chunked phases. Pattern, applied per phase:

1. The orchestrator writes a tight per-phase brief grounded in [PLAN.md](PLAN.md) + the current handoff doc, with **explicit instructions to verify external APIs via `WebFetch` before generating code** — to compensate for training-cutoff drift on AWS / CDK / SDK surfaces.
2. A sub-agent implements against the brief. After return, the orchestrator trust-but-verifies: reads all new files, re-runs tests + smoke + live `curl` against the deployed API.
3. Each phase commits atomically; the handoff doc is updated session-end so a fresh Claude session can resume cold.

**Three production incidents** are documented in [SESSION_HANDOFF.md](SESSION_HANDOFF.md) and shaped the patterns:
- "Out-of-band AWS change" — a sub-agent edited live Lambda config via CLI instead of through CDK source. Now every infra-touching brief is explicit: changes go through `infra/` + `cdk deploy`, with `cdk diff` clean as the success gate.
- Model deprecation — Claude 3 Haiku (the brief's recommendation) became LEGACY post-2026. Re-locked to Haiku 4.5 via inference profile.
- INSUFFICIENT_CONTEXT parsing — exact-match on the marker broke when Haiku 4.5 elaborated after the token; relaxed to `startswith` + strengthened the system prompt.

Sample documents (§5) were generated by Claude with a single-paragraph brief; the prompt is reproducible from `git log -- sample-docs/`.

---

## 9. Production hardening

**Implemented** in this submission:
- **Per-user identity** via Cognito JWT; per-user data isolation enforced by `actor_id`-keyed DDB + Memory.
- **Least-privilege IAM** on all four Lambdas + the AgentCore Runtime — specific ARNs, no wildcards; `s3:PutObject` scoped to `uploads/*` only; `bedrock:Invoke*` scoped to the Haiku 4.5 inference profile + the three foundation-model fan-out regions.
- **Structured JSON logs** with per-request `request_id` correlation across both Lambdas + APIGW + AgentCore Runtime.
- **Schema validation** (pydantic v2) at every request boundary; never leak exception detail.
- **30-day log retention**; AgentCore Memory `EventExpiryDuration=30 days`.
- **Secrets via Secrets Manager** — no secret values in synth output.
- **`autoDeleteObjects=True`** on the docs bucket + `RemovalPolicy.DESTROY` on most resources for clean tear-down.
- **Streaming token-level UX** + **runtime upload + ingestion** with progress feedback (Phase 9).

**Documented but not implemented** — production roadmap:
- **Per-user rate limits** + **APIGW WAF** (rate, geo, OWASP common rules).
- **VPC + interface endpoints** for Bedrock, Secrets Manager, S3, DynamoDB; both Lambdas in private subnets.
- **KMS CMKs** on docs bucket, all secrets, all log groups, DDB table, Memory.
- **X-Ray tracing** through APIGW + Lambda + AgentCore Runtime + Bedrock; budget alarms; per-user dashboards.
- **Bedrock model fallback** (Haiku → Sonnet with backoff); SnapStart for container Lambdas when GA; DLQ for failed invocations.
- **Event-driven background ingestion** (S3 PutObject → EventBridge → StartIngestionJob) as a complement to the explicit `/ingest` route — for bulk uploads where polling per file is too chatty.
- **AgentCore Gateway** for sharing tools across multiple agents; **AgentCore Identity** for per-tool fine-grained auth.
- **PII**: output redaction before logging; input PII scan + reject on `/query`.
- **RAG quality**: query-rewriting for ambiguous questions; reranking (Bedrock supports it); per-source recall + groundedness eval gate in CI.
- **Per-user upload isolation** — uploads currently land in a shared `uploads/` prefix; a real product would namespace by `actor_id` + filter KB retrieval accordingly.
- **AwsCustomResource secret-rotation fix** — bind the custom resource's `physical_resource_id` to a hash of the secret so it re-fires on rotation (currently requires a one-time manual `admin-set-user-password` — see [§7](#7-gotchas-from-the-live-deploy) #4).

---

## 10. Run instructions

Prereqs: AWS CLI configured, account with Bedrock access to Titan v2 + Haiku 4.5 inference profile enabled in `us-east-1`, Docker (for Lambda + agent image builds), Python 3.12, Node.js (for CDK CLI).

### 10.0 TL;DR — returning developer, two-command loop

After the one-time setup in [§10.1](#101-deploy-58-min-cold-longer-arm64-build-the-first-time) is done once on a machine, the day-to-day loop is exactly:

```bash
cd infra && env -u PYTHONPATH cdk deploy --all --require-approval never && cd ..
./scripts/run_streamlit.sh
```

The launcher reads live stack outputs from CloudFormation + Secrets Manager
and rewrites `streamlit_client/.streamlit/secrets.toml` automatically — IDs
that rotate on redeploy are picked up with no manual editing. Demo username
is `demo`; password is in Secrets Manager (one-liner in [§10.4](#104-run-the-local-streamlit-client)).

### 10.1 Deploy (~5–8 min cold; longer arm64 build the first time)

```bash
cd infra && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
env -u PYTHONPATH cdk bootstrap aws://<ACCOUNT_ID>/us-east-1

# Deploy all 4 stacks in dependency order
env -u PYTHONPATH cdk deploy --all --require-approval never \
  --outputs-file ../cdk-outputs.json
```

**Immediately after first deploy**: sync the test-user password (see [§7](#7-gotchas-from-the-live-deploy) #4):

```bash
PWD_ARN=$(jq -r '.AuthStack.TestUserPasswordSecretArn' ../cdk-outputs.json)
UPID=$(jq -r '.AuthStack.UserPoolId' ../cdk-outputs.json)
aws cognito-idp admin-set-user-password --region us-east-1 \
  --user-pool-id "$UPID" --username demo --permanent \
  --password "$(aws secretsmanager get-secret-value --secret-id "$PWD_ARN" --region us-east-1 --query SecretString --output text)"
```

### 10.2 Seed the bootstrap corpus (~15s)

```bash
cd .. && source venv/bin/activate
python scripts/upload_docs.py       # MD5-idempotent upload of sample-docs/
python scripts/start_ingestion.py   # ~10s for 6 docs
```

### 10.3 Validate (~2 min — the 90s upload+ingestion flow dominates)

```bash
python scripts/smoke_test.py        # 7 checks
python tests/eval/run_eval.py       # 8 questions, ~20s, writes tests/eval/eval_results.md
```

### 10.4 Run the local Streamlit client

A single launcher reads the live stack outputs from CloudFormation, pulls the
App Client secret from Secrets Manager, writes `secrets.toml`, and `exec`s
Streamlit. **No manual editing of `secrets.toml` is needed** — neither on
first run nor after a redeploy that rotates IDs.

```bash
./scripts/run_streamlit.sh
# → http://localhost:8501
```

What it does ([scripts/run_streamlit.sh](scripts/run_streamlit.sh)):
- `aws cloudformation describe-stacks` on `AuthStack` + `ApiStack` → reads
  `UserPoolId`, `UserPoolClientId`, `UserPoolClientSecretArn`, `ApiUrl`,
  `StreamFunctionUrl` (always live; never reads the on-disk `cdk-outputs.json`,
  which may be stale).
- `aws secretsmanager get-secret-value` → App Client secret.
- Rewrites `streamlit_client/.streamlit/secrets.toml` with every required
  `[auth]` / `[api]` / `[stream]` key populated. Preserves an existing
  `cookie_secret` across reruns so live Streamlit sessions survive.
- Launches `./venv/bin/python -m streamlit run streamlit_client/app.py`.

Idempotent and safe to run every session. If any output is missing it fails
fast with `error: output 'X' not found on stack 'Y'. Did you run cdk deploy?`.

To fetch the demo user's password manually (e.g. to paste into the Hosted UI):

```bash
PWD_ARN=$(aws cloudformation describe-stacks --region us-east-1 --stack-name AuthStack \
  --query "Stacks[0].Outputs[?OutputKey=='TestUserPasswordSecretArn'].OutputValue | [0]" --output text)
aws secretsmanager get-secret-value --region us-east-1 --secret-id "$PWD_ARN" \
  --query SecretString --output text
```

**Important**: Cognito App Client is provisioned with `http://localhost:8501/oauth2callback` as the **only** registered callback. Streamlit must bind to port 8501 — running on a different port breaks the OAuth round-trip.

### 10.4.1 What a user sees (browser walkthrough)

1. Open `http://localhost:8501` → Streamlit shows "Sign in with your Cognito account to continue" + a **Log in with Cognito** button.
2. Click it → browser redirects to the Cognito Hosted UI at `https://ragkb-244564.auth.us-east-1.amazoncognito.com/login?...`. Sign in as `demo` with the password from Secrets Manager (the same one the smoke test uses).
3. Cognito redirects back to `http://localhost:8501/oauth2callback`; Streamlit reads the ID token and persists it in `st.user.tokens["id"]`.
4. **Chat**: type a question in the bottom chat input. With "Stream responses" toggle ON (default), the answer streams in token-by-token via SSE — first token typically in 1–2s after the LWA container warms up (the very first call may take 4–6s for cold start; subsequent calls are 1–1.5s).
5. The Sources expander appears below each answer with up to `top_k` chunks (document, S3 URI, score, snippet).
6. **Sidebar — past conversations**: click a conversation name to load that session's transcript. Names are LLM-generated on the first turn of each session.
7. **Sidebar — upload**: pick a file (up to 50 MB, one of the 10 supported MIME types) and click "Ingest into knowledge base". A status panel streams the four steps (mint URL → S3 PUT → start ingestion → poll), reaching "Ingestion complete" in roughly 6–15s for a small document. Once complete, the new document is immediately queryable through both `/query` and `/query-stream`.
8. **Sign out**: the sidebar **Log out** button clears the cookie + redirects to Cognito's logout URL.

The UI supports: chat (with streaming toggle), past-session sidebar list, conversation replay on click, runtime document upload + ingest with status panel.

### 10.5 Tear down (no ongoing cost)

```bash
cd infra && source .venv/bin/activate
env -u PYTHONPATH cdk destroy --all --force
# AgentCore Runtime container costs ~$0.20-0.50/day idle if left up.
# Lambda's auto-created ECR repos may linger — `aws ecr delete-repository --force` if you want full zero.
```

**Cost**: idle ≈ **$0.40/month** (Secrets Manager flat fee + ECR storage) **plus AgentCore Runtime ~$0.20-0.50/day** while up. Active ≈ **$0.0003 per query** (Haiku tokens dominate). Cumulative project spend across all 9 phases stayed under **$2** of the $20 budget.

---

## 11. Repository layout

| Path | Purpose |
|---|---|
| [README.md](README.md) | This file. |
| [PLAN.md](PLAN.md) | Implementation plan (phases, decisions, risks). |
| [SESSION_HANDOFF.md](SESSION_HANDOFF.md) | Dense state-of-the-project doc, updated per phase. |
| [AWS Native Knowledge Base Agent Candidate Project Brief.md](AWS%20Native%20Knowledge%20Base%20Agent%20Candidate%20Project%20Brief.md) | Original brief. |
| [sample-docs/](sample-docs/) | 6 Markdown bootstrap docs. |
| [infra/](infra/) | CDK app. `stacks/{storage,auth,agent,api}_stack.py` + `constructs/{s3_vectors,bedrock_kb}.py`. |
| [lambda/](lambda/) | Buffered Lambda handler (RIC image). `app.py` routes 7 endpoints; `conversations.py` + `uploads.py` + `schemas.py`. |
| [lambda_stream/](lambda_stream/) | Streaming Lambda handler (LWA + FastAPI/uvicorn image). `stream_app.py` + `schemas.py`. |
| [agent/](agent/) | AgentCore Runtime container (arm64). `app.py` (`BedrockAgentCoreApp`) + `rag.py` + `schemas.py`. |
| [streamlit_client/](streamlit_client/) | Local Streamlit client (chat + sidebar + uploader). |
| [scripts/](scripts/) | `upload_docs.py`, `start_ingestion.py`, `smoke_test.py`, `get_id_token.py`. |
| [tests/](tests/) | `test_schemas.py`, `test_agent.py`, `test_stream.py`, `test_uploads.py` (unit, no AWS), `test_synth.py` (CDK synth), `eval/` (question set + harness + committed results). |
| [legacy/](legacy/) | Original Gemini + FAISS + Streamlit prototype, preserved for diff. |

---

## 12. Assumptions, known limitations & data flow

### 12.1 Assumptions

- AWS account `954863244564` in `us-east-1`. Region is hard-pinned in [infra/app.py](infra/app.py).
- Bedrock model access for Titan v2 + Haiku 4.5 inference profile is enabled in the account (one-time console step).
- Docker available locally for Lambda + agent image builds during `cdk deploy`. The agent container is arm64 — Buildx cross-compiles via QEMU on x86 Macs.
- The bootstrap corpus is the 6 sample-docs files. Authenticated users can add more at runtime via `/documents` + `/ingest`.

### 12.2 Known limitations

- **Shared corpus** — all authenticated users see all uploaded documents (single KB data source). Per-user namespacing is on the hardening list.
- **Ambiguous, multi-doc questions** can be incorrectly flagged INSUFFICIENT_ANSWER (Phase 6 eval q07). Fix path: query-rewrite or two-stage retrieve+rerank.
- **`venv/` at repo root** has stale `RAG-demo/` shebangs from the original directory rename; affects only the operator running `venv/bin/streamlit` (use `venv/bin/python -m streamlit` instead).
- **Test-user password sync** — first auth after a fresh deploy needs the one-time `admin-set-user-password` workaround (see [§7](#7-gotchas-from-the-live-deploy) #4).
- **AgentCore Runtime idle cost** — ~$0.20-0.50/day while the stack is up. Destroy with `cdk destroy --all` between active testing sessions.

### 12.3 Data flow & external services

**No user data leaves AWS in the runtime path.** Question + answer + sources travel through APIGW → Lambda → (AgentCore Runtime or Bedrock Runtime) → response, all inside AWS. AgentCore Memory + DDB are AWS-managed; user uploads land directly in S3 from the client via presigned URL (also AWS).

**Tokens are sent on every authenticated call** as `Authorization: Bearer …` over TLS. The streaming Lambda validates the token against the Cognito JWKS (also AWS) without any third-party call.

**CloudWatch Logs intentionally do NOT contain question/answer bodies** — only metadata (`event`, `request_id`, `latency_ms`, source count, status). This is a deliberate observability/PII tradeoff; production would add structured (redacted) logging behind a feature flag.

---

## 13. Testing & validation

| What | How | Where |
|---|---|---|
| Schema round-trip + validation | pytest cases for all request/response models | [tests/test_schemas.py](tests/test_schemas.py) |
| AgentCore agent path (Retrieve, confidence, Memory writes, INSUFFICIENT) | pytest with botocore Stubber + mocked AgentCore | [tests/test_agent.py](tests/test_agent.py) |
| Streaming Lambda (JWT verify, SSE framing, Claude streaming iteration) | pytest with mocked PyJWKClient + mocked stream EventStream | [tests/test_stream.py](tests/test_stream.py) |
| Upload + ingestion handlers (presign, key sanitize, MIME check, ConflictException) | pytest with mocked S3 + bedrock-agent clients | [tests/test_uploads.py](tests/test_uploads.py) |
| CDK synth invariants | pytest assertions on the synthesized template (2 Lambdas, Function URL with RESPONSE_STREAM, 7 routes, IAM scoped to `uploads/*`, etc.) | [tests/test_synth.py](tests/test_synth.py) |
| Live API contract + auth + streaming + upload+ingest | 7-check smoke test, exit-0 contract | [scripts/smoke_test.py](scripts/smoke_test.py) |
| End-to-end RAG quality | 8-question eval harness, writes markdown report | [tests/eval/run_eval.py](tests/eval/run_eval.py) |
| IaC drift | `cdk diff` (must show 0 differences at end of every phase) | manual |

Run pytest from the repo-root `venv/` for the non-synth tests; the synth tests need `infra/.venv` and a synthesized stack.

---

## 14. Closing

The brief asked for a "small, clean, well-explained implementation rather than a large unfinished one." This submission goes a bit beyond the minimum — the Phase-7 core (single-turn RAG + API-key auth + CDK + grounded answers + citations + confidence) is fully production-shaped, and Phases 8 + 9 add four optional extensions (Cognito JWT, AgentCore Runtime, AgentCore Memory + DDB chat history, streaming responses, runtime upload + ingestion) without compromising on any of the originals.

Live verification on 2026-05-25 confirmed: 7/7 smoke checks green, multi-turn DDB + Memory state in sync, streaming SSE end-to-end, runtime upload + ingestion + retrieval working with sub-3s grounded answers.

The full session-by-session build log lives in [SESSION_HANDOFF.md](SESSION_HANDOFF.md). The original implementation plan is in [PLAN.md](PLAN.md).

**Live IDs rotate per deploy.** Read them from [cdk-outputs.json](cdk-outputs.json). Run `python scripts/smoke_test.py` to verify the same end-to-end on your shell.
