# Architecture deep dive

Full ASCII diagram, per-stack responsibilities, request flow, API contract, RAG internals, and live-deploy gotchas. The main [README](../README.md) summarizes; this file is the reference.

---

## 1. ASCII architecture

```
┌─────────────────────────── LOCAL  (reviewer's machine) ───────────────────────────┐
│                                                                                   │
│   ┌──────────────────────┐   secrets.toml:                                        │
│   │  Streamlit chat UI   │     [api]    api_base_url = https://…/prod/            │
│   │  streamlit_client/   │     [stream] stream_url   = https://…lambda-url.aws/   │
│   │   • st.login(Cognito)│   At runtime: st.user.tokens["id"] = Cognito ID JWT    │
│   │   • Stream toggle    │   Sent on every call as Authorization: Bearer <jwt>    │
│   │   • File uploader    │                                                        │
│   │   • Sidebar history  │                                                        │
│   └─────────┬────────────┘                                                        │
│             │                                                                     │
│   (1)  POST /query          ─► API Gateway REST  (buffered, ≤30s)                 │
│   (1') POST /query-stream   ─► Lambda Function URL (SSE, streaming)               │
│   (1") POST /documents      ─► API Gateway REST  (mint presigned PUT URL)         │
│   (1"') PUT  to S3 directly with presigned URL (client-to-S3, no Lambda hop)      │
│   (1"")  POST /ingest + poll GET /ingest/{job_id}                                 │
└──────────┬──────────────────────────────────────────────────────────────────────┬─┘
           │ HTTPS (TLS 1.2+)                                                     │
           │                                                                      │
┌──────────┼─── AWS  (us-east-1) ──────────────────────────────────────────────────┐
│          ▼                                                                       │
│   ┌────────────────────────────────────────────────────────────────────────────┐ │
│   │  Cognito User Pool   (JWT issuer)                                          │ │
│   │   • Hosted UI                                                              │ │
│   │   • Pre-created test user `demo`                                           │ │
│   │   • App Client has a secret (SECRET_HASH on admin-initiate-auth)           │ │
│   └────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│   ┌──────────── REST API path ─────────────┐   ┌──── Function URL path ────────┐ │
│   │ API Gateway (regional, prod stage)     │   │ Lambda Function URL           │ │
│   │   CognitoUserPoolsAuthorizer on every  │   │   AuthType=NONE               │ │
│   │   non-/health route, 5-min cache       │   │   InvokeMode=RESPONSE_STREAM  │ │
│   │                                        │   │   CORS: localhost + Cloud     │ │
│   │   /health    GET    (no auth)          │   │   /query-stream  POST (SSE)   │ │
│   │   /query     POST                      │   │                               │ │
│   │   /conversations              GET      │   │   In-handler JWT verify       │ │
│   │   /conversations/{session_id} GET      │   │   (Cognito JWKS, pyjwt)       │ │
│   │   /documents POST   (mint presigned)   │   └────────────┬──────────────────┘ │
│   │   /ingest    POST   (StartIngestion)   │                │                    │
│   │   /ingest/{job_id} GET (poll)          │                │                    │
│   └────────────────┬───────────────────────┘                │                    │
│                    │                                        │                    │
│                    ▼                                        ▼                    │
│   ┌──────────────────────────────────┐     ┌──────────────────────────────────┐  │
│   │ Buffered Lambda                  │     │ Streaming Lambda                 │  │
│   │  lambda/   (x86_64, RIC image)   │     │  lambda_stream/  (x86_64, LWA +  │  │
│   │   • app.py routes 7 endpoints    │     │   uvicorn ASGI image)            │  │
│   │   • Proxy /query → AgentCore     │     │  • verifies Cognito ID token     │  │
│   │     Runtime via boto3            │     │    against User Pool JWKS        │  │
│   │   • /conversations* reads        │     │  • bedrock-runtime               │  │
│   │     Memory + DDB                 │     │    .InvokeModelWithResponseStream│  │
│   │   • /documents mints presigned   │     │  • emits SSE: meta → token → …   │  │
│   │     PUT URL (s3v4, 15-min TTL)   │     │    → sources → done              │  │
│   │   • /ingest calls bedrock-agent  │     │  • on stream-end writes DDB +    │  │
│   │     .StartIngestionJob           │     │    AgentCore Memory event        │  │
│   └─────────────┬────────────────────┘     └─────────────────┬────────────────┘  │
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
│       │              │              └────────── replayed by ─┴──────► /convers…  │
│       │              │                                                           │
│       │              └─────── invoked directly for streaming ───────────────────┘│
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

---

## 2. Request flow

A single user turn:

1. **Sign-in** — Streamlit calls `st.login()`; Cognito Hosted UI redirects back with an ID token in `st.user.tokens["id"]`. The token is sent as `Authorization: Bearer …` on every backend call.
2. **Stream toggle ON** (default): Streamlit POSTs `/query-stream` to the Lambda Function URL with the JWT and `{question, session_id, top_k}`. The streaming Lambda verifies the JWT against the Cognito JWKS, runs Retrieve, opens a Bedrock streaming completion, and emits SSE frames: `meta` (request_id, session_id, is_first_turn) → `token` ×N → `sources` → `done` (latency_ms, model_id, confidence, conversation_name). On stream end, the Lambda writes one event to AgentCore Memory (USER + ASSISTANT payload, `conversational` shape) and, on first turn, one row to DynamoDB.
3. **Stream toggle OFF**: Streamlit POSTs `/query` to the REST API. APIGW validates the JWT via its `CognitoUserPoolsAuthorizer`. The buffered Lambda proxies to AgentCore Runtime via `InvokeAgentRuntime`. The runtime container (arm64) runs Retrieve + InvokeModel + Memory/DDB writes inside `BedrockAgentCoreApp` and returns the full grounded response synchronously.
4. **Sidebar history** — Streamlit calls `GET /conversations` (DDB Query on `actor_id`) to list past sessions, and `GET /conversations/{session_id}` (paginated AgentCore Memory `ListEvents`) to replay one session's turns.
5. **Document upload** — user picks a file in the sidebar; Streamlit POSTs `/documents` to mint a presigned S3 PUT URL, PUTs the bytes directly to S3, then POSTs `/ingest` to start a Bedrock KB ingestion job, then polls `GET /ingest/{job_id}` every 2s. On `COMPLETE`, the new doc is immediately queryable.

---

## 3. Four CDK stacks

The CDK app at [infra/app.py](../infra/app.py) deploys four stacks in dependency order. Cross-stack refs are in-memory (single `cdk.App`), not `Fn.import_value`.

### 3.1 StorageStack — [infra/stacks/storage_stack.py](../infra/stacks/storage_stack.py)

- S3 docs bucket for `sample-docs/*.md` and runtime uploads under `uploads/`.
- S3 Vectors bucket + index, configured for Titan v2 (1024 dims, cosine, float32).
- Bedrock Knowledge Base + data source with FIXED_SIZE 300-token chunking, 20% overlap.
- DynamoDB `ConversationsTable` (PK `actor_id`, SK `session_id`, PAY_PER_REQUEST).
- AgentCore Memory for per-session conversational events (EventExpiryDuration=30 days).
- CDK-native sample-doc pre-seeding: `aws_s3_deployment.BucketDeployment` uploads the seed corpus on every deploy; a Lambda-backed `SeedKnowledgeBase` custom resource calls `bedrock-agent.StartIngestionJob` and polls. SHA-256 of the seed corpus drives update semantics (unchanged redeploys are no-ops); `prune=False` preserves user uploads.

### 3.2 AuthStack — [infra/stacks/auth_stack.py](../infra/stacks/auth_stack.py)

- Cognito User Pool with Hosted UI, no self-sign-up (admins create users; no public sign-up).
- App Client with a client secret (SECRET_HASH required on admin-initiate-auth).
- OAuth callbacks registered for both `http://localhost:8501/oauth2callback` and the Streamlit Cloud URL.
- Pre-created `demo` user; password auto-generated into Secrets Manager.
- Lambda-backed custom resource that calls `AdminSetUserPassword` to sync the generated password into Cognito on every deploy where its properties diff (Phase 11 hardening — see Gotcha #4 below).

### 3.3 AgentStack — [infra/stacks/agent_stack.py](../infra/stacks/agent_stack.py)

- arm64 `DockerImageAsset` built from [agent/](../agent/).
- Deploys a Bedrock AgentCore Runtime running `BedrockAgentCoreApp`.
- Runtime IAM is least-privilege: Retrieve on the KB ARN only, InvokeModel on the Haiku 4.5 inference profile + the 3 foundation-model fan-out region ARNs, CreateEvent + ListEvents on the Memory ARN, PutItem on the DDB table.

### 3.4 ApiStack — [infra/stacks/api_stack.py](../infra/stacks/api_stack.py)

- Buffered Lambda from [lambda/](../lambda/) (x86_64, RIC image) behind API Gateway REST.
- Streaming Lambda from [lambda_stream/](../lambda_stream/) (x86_64, LWA + FastAPI/uvicorn) exposed through a Lambda Function URL with `InvokeMode=RESPONSE_STREAM`.
- `CognitoUserPoolsAuthorizer` on every non-`/health` REST route, 5-min token cache.
- In-handler JWT verification (pyjwt + JWKS) on the streaming route — Function URL `AuthType=NONE` because Streamlit cannot SigV4-sign.
- Per-method APIGW throttling caps (`/query` 10/20, `/ingest` 5/10, stage backstop 20/40).
- Multi-origin CORS allowing local Streamlit + Streamlit Community Cloud.
- 30-day CloudWatch log retention; structured JSON logs.

---

## 4. API contract

### 4.1 Endpoints

| Method | Path | Transport | Auth | Purpose |
|---|---|---|---|---|
| `GET`  | `/health` | REST | none | Liveness probe; no AWS calls. |
| `POST` | `/query`  | REST | Cognito JWT | Buffered grounded RAG answer (≤30s). |
| `POST` | `/query-stream` | Function URL (SSE) | Cognito JWT (in-handler verify) | Streaming grounded RAG answer. |
| `GET`  | `/conversations` | REST | Cognito JWT | List past sessions for the calling user. |
| `GET`  | `/conversations/{session_id}` | REST | Cognito JWT | Replay one session's turns. |
| `POST` | `/documents` | REST | Cognito JWT | Mint a presigned S3 PUT URL for one upload. |
| `POST` | `/ingest` | REST | Cognito JWT | Start a Bedrock KB ingestion job. |
| `GET`  | `/ingest/{job_id}` | REST | Cognito JWT | Poll an ingestion job's status. |

### 4.2 `/query` request

```json
{ "question": "What is the refund window for monthly plans?", "session_id": "s-abc123", "top_k": 5 }
```

- `question` — string, required, min length 1.
- `session_id` — string, optional. If omitted the API generates one. Reusing it across calls accumulates turns under one conversation in AgentCore Memory.
- `top_k` — int, optional, default 5, range 1–20.

### 4.3 `/query` 200 response

```json
{
  "answer": "According to the Acme Notes Refund Policy, the refund window for monthly plans is **30 days** … [1]",
  "confidence": 0.854,
  "sources": [
    { "s3_uri": "s3://…/refund-policy.md", "document": "refund-policy.md", "score": 0.872, "snippet": "…" }
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

`conversation_name` is LLM-generated on the first turn of a session (3–5 words, ≤50 chars) and persisted to DDB. On turns 2+ it is `null` and the existing DDB row is not overwritten.

### 4.4 `/query-stream` SSE wire format

```
event: meta
data: {"request_id":"…","session_id":"…","is_first_turn":true}

event: token
data: {"text":"According"}

event: sources
data: {"sources":[{...}]}

event: done
data: {"latency_ms":2497,"model_id":"…","confidence":0.672,"conversation_name":"Monthly Plan Refund Policy"}
```

On error the stream ends with a single `event: error` frame. Auth failures return JSON (HTTP 401) — the client should check status before opening the event loop.

If the model emits `INSUFFICIENT_CONTEXT`, the streaming Lambda appends a canned answer as a final `token` frame and clamps `confidence` to 0.2 in `done`. The Streamlit client strips the marker prefix on display.

### 4.5 Upload + ingestion

```http
POST /documents
{"filename": "engineering-roadmap.md", "content_type": "text/markdown"}
→ 200 {"upload_url": "https://…", "key": "uploads/2026-05-25/abc-engineering-roadmap.md", "expires_in": 900}

PUT  <upload_url>           (client → S3 directly, must echo same Content-Type)

POST /ingest
{"key": "uploads/2026-05-25/abc-engineering-roadmap.md", "client_token": null}
→ 200 {"job_id": "26EXBYYLCZ", "status": "STARTING"}

GET  /ingest/26EXBYYLCZ
→ 200 {"job_id": "…", "status": "COMPLETE", "statistics": {"scanned": 8, "indexed": 1, "failed": 0}}
```

Allowed extensions and MIME types (cross-checked server-side at `/documents`): `.txt`, `.md`, `.html`/`.htm`, `.csv`, `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`. Soft client cap of 50 MB. Keys are sanitized to `[A-Za-z0-9._-]` and prefixed with `uploads/{yyyy-mm-dd}/{uuid4-hex}-` to avoid collisions.

### 4.6 Error envelope

```json
{ "error": "InvalidRequest", "message": "…detail…", "request_id": "…" }
```

| Status | `error` | When |
|---:|---|---|
| 400 | `InvalidRequest` | Bad JSON, missing/empty `question`, `top_k` out of range, bad MIME, key not under `uploads/`. |
| 401 | (Cognito) | Missing/expired/invalid JWT. APIGW authorizer rejects before Lambda runs; Function URL rejects in-handler. |
| 404 | `InvalidRequest` | Ingestion `job_id` not found. |
| 409 | `InvalidRequest` | `client_token` already used for a different ingestion. |
| 500 | `Internal` | Bedrock `ClientError` or unhandled exception. Generic message; full traceback in CloudWatch keyed by `request_id`. |

### 4.7 Authentication flow

- **Token issuance**: Cognito User Pool issues an ID JWT signed RS256. `st.login()` runs OAuth Code flow against the Hosted UI; tokens land on `st.user.tokens["id"]`.
- **REST validation**: APIGW's `CognitoUserPoolsAuthorizer` validates signature, `aud`, `iss`, `exp` before invoking Lambda. Claims are exposed at `event.requestContext.authorizer.claims`.
- **Function URL validation**: the streaming Lambda parses `Authorization: Bearer …` and validates against the User Pool JWKS (`pyjwt[crypto]` + `PyJWKClient`). Explicitly checks `token_use == "id"` so an access token can't be substituted.
- **Per-user identity**: the `sub` claim is used as the `actor_id` (DDB primary key + AgentCore Memory `actorId`). No two users can see each other's sessions.
- **Pre-created test user**: AuthStack creates `demo` with a password generated into Secrets Manager. Operator scripts fetch the password + App Client secret, then call `admin-initiate-auth` with `SECRET_HASH` to get a fresh ID token.

---

## 5. RAG behavior

### 5.1 Retrieval

`bedrock-agent-runtime.Retrieve(knowledgeBaseId, retrievalQuery, vectorSearchConfiguration={numberOfResults: top_k})`. Returns `[{content, score, location.s3Location.uri}, …]`. Filename is extracted into the `document` field.

### 5.2 Prompt construction

System prompt (verbatim, lives in both [agent/rag.py](../agent/rag.py) and [lambda_stream/stream_app.py](../lambda_stream/stream_app.py)):

> Answer ONLY from the provided context. If the context is insufficient to answer the question, your entire response MUST be the single token `INSUFFICIENT_CONTEXT` with no other characters, words, or explanation. When you can answer, cite sources inline as [n].

User message: numbered chunks (`[1] …  [2] …`) followed by `Question: <user question>`. Model is invoked via Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) — the US cross-region inference profile fans across us-east-1 / us-east-2 / us-west-2.

### 5.3 Confidence

```
confidence = clamp(0.6·max(scores) + 0.3·mean(scores) + 0.1·(1 − stdev(scores)), 0, 1)
```

Top-match score dominates (60%); mean rewards consistent matching (30%); stdev penalty prefers tight clusters over a single outlier hit (10%).

### 5.4 INSUFFICIENT_CONTEXT override

If the model's answer **starts with** `INSUFFICIENT_CONTEXT` (Haiku 4.5 sometimes elaborates after the token), the response is rewritten to `"I don't have information about that in the knowledge base."` and confidence is clamped to ≤ 0.2. Streaming path: the marker tokens flow to the client; the streaming Lambda then appends the canned answer + clamps confidence in `done`; the Streamlit consumer strips the prefix on display.

### 5.5 Per-session memory

Every turn writes **one event** to AgentCore Memory under `(actor_id, session_id)`, with a native `conversational` payload:

```json
[
  {"conversational": {"content": {"text": "<user question>"},   "role": "USER"}},
  {"conversational": {"content": {"text": "<assistant answer>"}, "role": "ASSISTANT"}}
]
```

`is_first_turn` is detected by `list_events(maxResults=1)`. On first turn only, the agent writes a single DDB row with `conversation_name` + `created_at` (Unix epoch int). Sidebar reads from DDB (fast `Query`); replay reads from Memory (paginated `ListEvents`).

A third `blob` payload item carries base64-encoded JSON: `{sources, confidence, latency_ms, model_id, retrieval_strategy}`. On replay, [lambda/conversations.py](../lambda/conversations.py) decodes that sidecar and attaches it as a `payload` field on the ASSISTANT message so the UI can re-render the confidence badge + sources expander, not just the answer text. (Base64 is a workaround for an AgentCore Memory Document-type round-trip quirk — see Gotcha #7 below.)

---

## 6. Why AgentCore Runtime + Memory

The brief calls out Bedrock Agents / AgentCore as an acceptable orchestration layer; this project uses both AgentCore Runtime (managed agent container) and AgentCore Memory (managed conversation state) deliberately, even though pure Lambda would have met the single-tool RAG case.

The trade-off:

- **Cost**: AgentCore Runtime idles at ~$0.20–0.50/day while the stack is up. The buffered `/query` path also pays ~100–300ms for the extra Lambda → AgentCore hop.
- **Benefit**: console-introspectable conversation events, managed runtime container with per-actor scoping, and the production-shaped pieces (Memory + Runtime + IAM) that a real multi-tool agent would need.
- **Decision**: acceptable for a demo + interview submission; for pure single-tool RAG in real production, pure Lambda is cheaper and simpler. The streaming path bypasses AgentCore Runtime entirely (it calls `bedrock-runtime.InvokeModelWithResponseStream` directly) because `InvokeAgentRuntime` is buffered-only as of May 2026.

No LangGraph or other framework weight — pure boto3 inside `BedrockAgentCoreApp`. The full decision matrix (and the six concrete improvements over `aws-samples/sample-ai-agent-architectures-agentcore`) is in [PLAN.md](../PLAN.md) and the Phase 8 record in [SESSION_HANDOFF.md](../SESSION_HANDOFF.md).

---

## 7. Gotchas from the live deploy

Eight AWS-side gotchas have hit this project across phases. They will hit again on a fresh redeploy if not handled:

1. **AgentCore Runtime is arm64-only.** `DockerImageAsset` must build `linux/arm64`. From an x86 Mac the build cross-compiles via Docker Buildx + QEMU (slower first build).
2. **Cognito Hosted UI domain prefixes cannot contain `aws`, `amazon`, or `cognito`.** The project uses `ragkb-{account-suffix}`.
3. **DynamoDB returns `Decimal` for numbers.** Coerce to `int`/`float` before `json.dumps` (see [lambda/conversations.py](../lambda/conversations.py)).
4. **`AwsCustomResource` + `SecretValue.unsafe_unwrap()` does NOT inject the secret value into Custom Resource calls.** CFN [does not resolve `secretsmanager` dynamic references](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/dynamic-references-secretsmanager.html) inside Custom Resource properties, so the literal `{{resolve:secretsmanager:ARN:SecretString:::}}` string was passed verbatim to `AdminSetUserPassword`. The Phase 11 fix replaces it with a small Lambda-backed Custom Resource that reads the secret via `boto3.client("secretsmanager").get_secret_value(...)` at runtime — see [infra/stacks/auth_stack.py](../infra/stacks/auth_stack.py). The CR re-fires on every deploy where its ARN-bound properties diff.
5. **Python Lambda has no native response streaming** (as of May 2026 — only Node.js does). The streaming Lambda ships AWS Lambda Web Adapter (LWA) + FastAPI/uvicorn inside the container; the Function URL is `InvokeMode=RESPONSE_STREAM` with `AWS_LWA_INVOKE_MODE=response_stream`.
6. **Presigned PUT requires the client to echo the exact `Content-Type` it was signed with**, or S3 returns 403 `SignatureDoesNotMatch`. The presigned URL is also SigV4 (`signature_version="s3v4"`) explicitly so the legacy SigV2 fallback can't break the contract.
7. **AgentCore Memory `blob` payload items round-trip poorly.** The Smithy `Document` type accepts any JSON shape on `CreateEvent`, but `ListEvents` returns it to boto3 as a Python `str` of the Java SDK's `toString()` repr — unquoted keys, `=` separators, not parseable as JSON. The persistence layer encodes the per-turn metadata as urlsafe-base64 inside a single Document field; on read, [lambda/conversations.py](../lambda/conversations.py) regex-extracts and decodes.
8. **Every `cdk destroy --all` + redeploy rotates Cognito IDs, the App Client secret, and both URLs.** Local dev: always launch via [`./scripts/run_streamlit.sh`](../scripts/run_streamlit.sh) — it reads live values from CloudFormation + Secrets Manager on every run. Streamlit Cloud: re-run `python scripts/print_streamlit_cloud_secrets.py | pbcopy` and re-paste into the Streamlit Cloud Secrets editor (no auto-sync; Streamlit Cloud secrets are dashboard-managed).

---

## 8. Cost notes

- **Idle** ≈ **$0.40/month** (Secrets Manager flat fee + ECR storage) **plus AgentCore Runtime ~$0.20–0.50/day** while up.
- **Active** ≈ **$0.0003 per query** (Haiku tokens dominate; APIGW + Lambda compute are rounding error).
- Cumulative project spend across all phases stayed under **$3** of the $20 budget.

Destroy with `cdk destroy --all` between active testing sessions to zero the AgentCore Runtime daily charge.
