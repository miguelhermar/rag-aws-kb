# RAG-AWS — Knowledge Base Agent on AWS

A small, production-shaped, AWS-native retrieval-augmented agent. A Streamlit chat client signs in with Cognito and calls a CDK-deployed API that fronts a Bedrock Knowledge Base (S3 Vectors) and Claude Haiku 4.5, with per-session conversation memory, streaming responses, and runtime document upload + ingestion.

- **Live demo**: https://rag-aws-kb.streamlit.app (Streamlit Community Cloud)
- **Region**: `us-east-1`
- **Status**: smoke test passes 7/7; multi-turn DDB + AgentCore Memory state validated live.

This repository is a productionization of a Streamlit + Gemini + FAISS prototype (preserved under [legacy/](legacy/)). It satisfies the AWS-Native Knowledge Base Agent take-home brief and lands six of the optional extensions (Cognito auth, AgentCore Runtime, AgentCore Memory, DynamoDB chat history, streaming responses, runtime upload + ingestion), plus per-method APIGW throttling.

---

## 1. Architecture

![Architecture](docs/architecture.png)

A reviewer's machine runs the Streamlit client (or hits the public Streamlit Cloud URL). On sign-in, Cognito issues an ID JWT that the client sends as `Authorization: Bearer …` on every backend call. Two parallel paths handle questions:

- **Buffered `/query`** — API Gateway REST → Lambda (proxy) → AgentCore Runtime (arm64 container) → Retrieve (KB) + InvokeModel (Haiku 4.5) → response.
- **Streaming `/query-stream`** — Lambda Function URL (SSE) → streaming Lambda (LWA + uvicorn) → `InvokeModelWithResponseStream` → token-by-token SSE frames.

Both paths write one event per turn to AgentCore Memory and a metadata row to DynamoDB (first turn only). Document uploads use a presigned S3 PUT URL minted by Lambda; ingestion is triggered via a separate `/ingest` route that calls `bedrock-agent.StartIngestionJob`.

A full ASCII diagram, per-stack breakdown, request-flow narrative, and complete API contract live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### 1.1 UI

![UI](docs/ui.png)

Chat input with streaming toggle, past-conversations sidebar with replay-on-click, runtime document upload with progress, Cognito sign-in / sign-out. Built on `st.login()`, `st.chat_message`, `st.dialog`, and `st.file_uploader`.

### 1.2 Key design decisions

| Decision | Chose | Over | Why |
|---|---|---|---|
| Vector store | **S3 Vectors** (native to Bedrock KB) | OpenSearch Serverless | Brief forbids OSS; S3 Vectors is the only GA, low-idle-cost AWS-managed vector option. |
| LLM | **Claude Haiku 4.5** via cross-region inference profile | Sonnet; Claude 3 Haiku | Brief recommended Claude 3 Haiku, but it became LEGACY post-2026. Haiku 4.5 is the modern equivalent; inference profile gives multi-region failover for free. |
| Auth | **Cognito User Pool + JWT** | API key + UsagePlan | Per-user identity needed for `actor_id` (Memory + DDB primary key). |
| Conversation state | **AgentCore Memory + DDB metadata** | Single store | AgentCore Memory holds the *messages* (console-introspectable, native `conversational` shape); DDB holds *metadata* for fast sidebar listing. |
| Agent runtime | **AgentCore Runtime + arm64 container** | Pure Lambda; LangGraph | Pure Lambda would meet the brief. AgentCore Runtime is chosen to demonstrate the AgentCore primitives (managed runtime + Memory + per-actor scoping). Cost: ~$0.20–0.50/day idle; ~100–300ms extra hop on buffered `/query`. |
| Streaming transport | **Lambda Function URL + SSE** (LWA + uvicorn) | APIGW WebSocket; direct AgentCore | REST API can't stream; Python Lambda has no native streaming. LWA + Function URL is the only AWS-native path. |
| Streaming source | **`bedrock-runtime.InvokeModelWithResponseStream`** directly | AgentCore Runtime streaming | `InvokeAgentRuntime` is buffered-only as of May 2026. The streaming Lambda writes Memory + DDB itself after the stream completes. |
| Upload | **Presigned S3 PUT URL** | Multipart-through-Lambda | No Lambda payload cap; AWS-native pattern. Client PUTs to S3 directly. |
| Chunking | **FIXED_SIZE 300 tokens, 20% overlap** | Semantic; hierarchical | Sample docs are short policy markdown; fixed chunking is predictable, debuggable, adequate. |
| IaC | **AWS CDK Python, 4 stacks** | Single stack | Each stack redeploys independently — the slow arm64 image build only re-runs when agent code changes. |

Full deep dive: [docs/ARCHITECTURE.md §6](docs/ARCHITECTURE.md#6-why-agentcore-runtime--memory).

---

## 2. AWS services

| Service | Role |
|---|---|
| **Cognito User Pool + App Client** | Issues ID JWTs; Hosted UI sign-in; one pre-created `demo` test user. |
| **API Gateway** (REST, regional) | Public entry point. `CognitoUserPoolsAuthorizer` on every non-`/health` route. |
| **Lambda — buffered** (container) | Routes `/query` (proxy to AgentCore Runtime), `/conversations*`, `/documents`, `/ingest*`. |
| **Lambda — streaming** (container, LWA + uvicorn) | Hosts `/query-stream` via Function URL with `InvokeMode=RESPONSE_STREAM`. |
| **AgentCore Runtime** (arm64 container) | Hosts the agent code. `BedrockAgentCoreApp` wraps Retrieve + InvokeModel + Memory + DDB writes. |
| **AgentCore Memory** | Per-session conversational events. `EventExpiryDuration=30 days`. |
| **DynamoDB** (`ConversationsTable`) | Per-user conversation metadata (`conversation_name`, `created_at`). |
| **Bedrock Knowledge Base** | Chunks + embeds docs; serves `Retrieve`. |
| **Bedrock Runtime** | Haiku 4.5 inference (buffered + streaming). |
| **S3 docs bucket** | Source docs + `uploads/` (presigned-PUT target). |
| **S3 Vectors** | Native vector store backing the KB. dim=1024, cosine, float32. |
| **Secrets Manager** | App Client secret + generated demo-user password. |
| **CloudWatch Logs** | Structured JSON for both Lambdas + APIGW access logs. 30-day retention. |

The CDK app deploys these into four stacks — `StorageStack`, `AuthStack`, `AgentStack`, `ApiStack`. Per-stack responsibilities are in [docs/ARCHITECTURE.md §3](docs/ARCHITECTURE.md#3-four-cdk-stacks).

---

## 3. Authentication

Cognito User Pool with Hosted UI; no self-sign-up (admins create users via the CLI). The App Client has a client secret, so `admin-initiate-auth` requires a `SECRET_HASH`. AuthStack pre-creates a `demo` user with a password generated into Secrets Manager.

REST routes are gated by API Gateway's `CognitoUserPoolsAuthorizer` (validates signature, `aud`, `iss`, `exp` before invoking Lambda). The streaming Function URL is `AuthType=NONE` — Streamlit can't SigV4-sign — so the streaming Lambda parses `Authorization: Bearer …` and validates the JWT against the Cognito JWKS in-handler (`pyjwt[crypto]` + `PyJWKClient`). Both paths use the JWT `sub` claim as the `actor_id` (DDB primary key + AgentCore Memory `actorId`). No two users can see each other's sessions.

API keys and UsagePlans were removed entirely in Phase 8; per-method APIGW throttling replaces them as an account-wide abuse cap.

---

## 4. RAG / agent behavior

**Retrieval** — `bedrock-agent-runtime.Retrieve` returns the top-`k` chunks with relevance scores.

**Prompting** — the agent constructs a numbered context block (`[1] …  [2] …`) and a strict system prompt that forces grounding:

> Answer ONLY from the provided context. If the context is insufficient to answer the question, your entire response MUST be the single token `INSUFFICIENT_CONTEXT` with no other characters, words, or explanation. When you can answer, cite sources inline as [n].

**INSUFFICIENT_CONTEXT override** — if the model's reply starts with the marker (Haiku 4.5 sometimes elaborates after it), the response is rewritten to `"I don't have information about that in the knowledge base."` and confidence is clamped to ≤ 0.2.

**Confidence** — a transparent function of the retrieval scores:

```
confidence = clamp(0.6·max(scores) + 0.3·mean(scores) + 0.1·(1 − stdev(scores)), 0, 1)
```

Top-match score dominates (60%); mean rewards consistent matching (30%); stdev penalty prefers tight clusters over a single outlier hit.

**Per-session memory** — every turn writes **one event** to AgentCore Memory under `(actor_id, session_id)` in the native `conversational` shape (USER + ASSISTANT items) plus a base64-encoded `blob` sidecar with `{sources, confidence, latency_ms, model_id}` so reloading a past conversation can re-render the full assistant payload (not just text). On first turn only, the agent writes one DDB row with an LLM-generated `conversation_name` (3–5 words) for sidebar listing.

Prompt, confidence formula, and Memory shape detail: [docs/ARCHITECTURE.md §5](docs/ARCHITECTURE.md#5-rag-behavior).

---

## 5. Sample documents

The KB is seeded with 6 Markdown documents under [sample-docs/](sample-docs/), each 3–4 KB, under a fictional brand **Acme Notes** (collaborative-note SaaS, domain `acmenotes.example`):

| File | Topic |
|---|---|
| [refund-policy.md](sample-docs/refund-policy.md) | Refund windows, eligibility, exclusions. |
| [shipping-policy.md](sample-docs/shipping-policy.md) | Notebook shipping rates + timing. |
| [security-policy.md](sample-docs/security-policy.md) | Authentication, encryption, incident response. |
| [employee-handbook.md](sample-docs/employee-handbook.md) | PTO, benefits, work expectations. |
| [product-faq.md](sample-docs/product-faq.md) | Plan comparison, feature availability. |
| [acceptable-use.md](sample-docs/acceptable-use.md) | Prohibited content + behavior. |

Generated by Claude under a single-paragraph brief: realistic enterprise-policy tone, no PII, no real-brand collisions. The corpus is intentionally small and topically diverse so retrieval behavior is easy to reason about during evaluation.

Authenticated users can add new documents at runtime via the `/documents` → `/ingest` flow.

---

## 6. Ingestion workflow

Seeding is fully automatic on `cdk deploy`. `StorageStack` uses `aws_s3_deployment.BucketDeployment` to push `sample-docs/*.md` into the docs bucket (`prune=False` so user uploads at `uploads/...` survive), then a Lambda-backed `SeedKnowledgeBase` custom resource calls `bedrock-agent.StartIngestionJob` and polls until `COMPLETE`. A SHA-256 of the seed corpus is passed as a CR property so unchanged redeploys are no-ops (no wasted Titan embedding spend); changing any seed file triggers a delta ingest.

Runtime uploads follow the same `StartIngestionJob` API, but triggered per-upload by an authenticated user via the REST routes. `client_token` is auto-generated for idempotent retries. The Bedrock KB chunks each doc with **FIXED_SIZE 300 tokens / 20% overlap**, embeds with **Titan v2 (1024-dim)**, and writes vectors to S3 Vectors. Typical wall-clock: ~20s for the 6 seed docs; ~6–15s for a small user upload.

---

## 7. Changes from the sample project

The original prototype (preserved under [legacy/](legacy/)) was a single-process Streamlit app using Gemini embeddings + FAISS retrieval + Gemini generation, with a partial FastAPI/Next.js stack. Every layer was rewritten for AWS-native operation:

| Layer | Sample prototype | This solution | Why |
|---|---|---|---|
| Compute | Single-process Python | Lambda containers + AgentCore Runtime container | Serverless + managed agent runtime; honors the $20 budget. |
| LLM | Gemini | Claude Haiku 4.5 (Bedrock) | Brief mandate (AWS-native). |
| Embeddings | Gemini | Titan v2 (1024-dim) | Required by S3 Vectors + Bedrock KB. |
| Vector store | FAISS in-process | S3 Vectors via Bedrock KB | Brief forbids OSS; S3 Vectors is the only managed AWS option. |
| Auth | None (open localhost) | Cognito User Pool + JWT | Brief mandate (authenticated API). |
| Storage | Local filesystem | S3 + DynamoDB | Brief mandate (S3 source + structured chat history). |
| IaC | `docker-compose` + `setup.sh` | AWS CDK Python (4 stacks) | Brief mandate. |
| API | FastAPI on localhost | API Gateway REST + Lambda Function URL (SSE) | Brief mandate (AWS-hosted API). |
| Conversation state | Session-scoped Python dict | AgentCore Memory + DynamoDB metadata | Persisted, per-user, console-introspectable. |
| Sources | In-memory FAISS results | S3-URI-referenced response with score + snippet | Durable, citable, auditable. |
| Confidence | Keyword + length + score blend | Simplified score-based clamp | Removed legacy heuristics that don't apply to Titan/Bedrock scoring. |
| Secrets | Leaked Gemini key in `.env` | Secrets Manager + auto-sync launcher | Production-grade, no plaintext. |

The grounded-prompt shape and confidence-formula skeleton were ported from [legacy/rag/generator.py](legacy/rag/generator.py) (lines 28–74 and 217–264); everything else was rebuilt.

---

## 8. AI tools used during development

Built using **Claude Code** (Anthropic's CLI) running Opus 4.7 as the orchestrating agent, with `general-purpose` sub-agents handling chunked phases. Pattern, applied per phase:

1. The orchestrator wrote a tight per-phase brief grounded in [PLAN.md](PLAN.md) and the current session handoff, with explicit instructions to verify external APIs via `WebFetch` before generating code — to compensate for training-cutoff drift on AWS / CDK / SDK surfaces.
2. A sub-agent implemented against the brief. After return, the orchestrator trust-but-verified: read all new files, re-ran tests + smoke + live `curl` against the deployed API.
3. Each phase commits atomically; the session handoff doc is updated at session-end so a fresh Claude session can resume cold.

Three production incidents documented in [SESSION_HANDOFF.md](SESSION_HANDOFF.md) shaped the patterns (out-of-band AWS change → CDK-only rule; Claude 3 Haiku deprecation → relock to Haiku 4.5; INSUFFICIENT_CONTEXT exact-match → relaxed to `startswith`). The 6 sample documents were also generated by Claude.

---

## 9. Data flow and known limitations

**No user data leaves AWS in the runtime path.** Question, answer, and sources travel through APIGW → Lambda → (AgentCore Runtime or Bedrock Runtime) → response, all inside AWS. AgentCore Memory + DDB are AWS-managed; user uploads land directly in S3 from the client via presigned URL. No third-party APIs are called at runtime.

Tokens are sent on every authenticated call as `Authorization: Bearer …` over TLS; the Cognito JWKS lookup is also AWS-internal. CloudWatch Logs intentionally do **not** contain question/answer bodies — only metadata (`event`, `request_id`, `latency_ms`, source count, status). A deliberate observability/PII tradeoff.

Known limitations:

- **Shared corpus** — all authenticated users see all uploaded documents (single KB data source). Per-user namespacing is on the [hardening roadmap](docs/HARDENING.md).
- **Ambiguous, multi-doc questions** can be incorrectly flagged INSUFFICIENT_ANSWER (see q07 in §10). Fix path: query-rewrite or two-stage retrieve+rerank.
- **AgentCore Runtime idle cost** — ~$0.20–0.50/day while the stack is up. Destroy with `cdk destroy --all` between testing sessions.
- **Streamlit Cloud secrets are dashboard-managed** — after every `cdk destroy --all` + redeploy, re-paste from `scripts/print_streamlit_cloud_secrets.py`. Local dev uses `scripts/run_streamlit.sh`, which auto-syncs.
- **`agent/rag.py` ↔ `lambda_stream/stream_app.py` duplication** — both paths share the system prompt + INSUFFICIENT_CONTEXT handling + confidence formula + Memory/DDB writes because Docker contexts are isolated and `InvokeAgentRuntime` is buffered-only. Tracked under [HARDENING](docs/HARDENING.md).

The eight live-deploy gotchas (arm64-only AgentCore, Cognito domain reserved prefixes, DDB `Decimal` JSON, `AwsCustomResource` + secret references, Python Lambda streaming, presigned-PUT Content-Type, AgentCore Memory blob round-trip, redeploy-rotates-IDs) are documented at [docs/ARCHITECTURE.md §7](docs/ARCHITECTURE.md#7-gotchas-from-the-live-deploy).

---

## 10. Testing and validation

| What | Where |
|---|---|
| Schema round-trip + validation | [tests/test_schemas.py](tests/test_schemas.py) |
| AgentCore agent path (Retrieve, confidence, Memory writes, INSUFFICIENT) | [tests/test_agent.py](tests/test_agent.py) |
| Streaming Lambda (JWT verify, SSE framing, Claude streaming) | [tests/test_stream.py](tests/test_stream.py) |
| Upload + ingestion handlers (presign, key sanitize, MIME check) | [tests/test_uploads.py](tests/test_uploads.py) |
| CDK synth invariants (2 Lambdas, Function URL `RESPONSE_STREAM`, 7 routes, IAM scoping) | [tests/test_synth.py](tests/test_synth.py) |
| Live API contract + auth + streaming + upload + ingest (7 checks) | [scripts/smoke_test.py](scripts/smoke_test.py) |
| End-to-end RAG quality (8 questions, writes markdown report) | [tests/eval/run_eval.py](tests/eval/run_eval.py) |

### 10.1 Sample queries (live results)

Five representative queries from the committed [tests/eval/eval_results.md](tests/eval/eval_results.md) (full 8-question table in that file), chosen to span the behavior spectrum:

| Question | Expected doc | Top source | Confidence | Behavior |
|---|---|---|---:|---|
| What kinds of content are prohibited under the Acme Notes acceptable use policy? | acceptable-use.md | ✅ acceptable-use.md | **0.899** | High-confidence grounded answer with `[1]` citation. |
| What authentication requirements does Acme Notes enforce for employee accounts? | security-policy.md | ✅ security-policy.md | **0.867** | High-confidence multi-bullet answer. |
| What is the refund window for monthly plans? | refund-policy.md | ✅ refund-policy.md | **0.668** | Mid-confidence grounded answer (shorter context). |
| What happens to my data if I cancel my account? | (ambiguous) | acceptable-use.md | **0.200** | INSUFFICIENT_CONTEXT override fires; canned answer + clamped confidence. |
| What is the office Wi-Fi password? | (off-corpus) | security-policy.md | **0.200** | Off-corpus rejected; canned answer + clamped confidence. |

Aggregate from the 8-question eval: **source-match 100%** on the 6 in-corpus questions, **mean confidence 0.813** in-corpus, **0.200** on off-corpus (clamped).

### 10.2 Smoke test contract

`scripts/smoke_test.py` runs against the live deployed API and exits 0 on:

1. `/health` 200 with body match.
2. `/query` 200 with valid JWT, schema-valid response, non-empty sources, confidence in [0,1].
3. `/query` 401 with no token (Cognito authorizer rejects).
4. `/query` 400 on empty question (`error == "InvalidRequest"`).
5. `/conversations` 200 returns the calling user's sessions.
6. `/query-stream` 200 with valid SSE framing (`meta → token → sources → done`).
7. Full upload + ingestion + retrieval flow (~90s) — mint URL → S3 PUT → start ingestion → poll to `COMPLETE` → query and verify the new doc is cited.

### 10.3 What I'd improve next

The eval surfaced two honest weaknesses that a follow-up iteration would target:

- **Ambiguous questions are over-rejected.** q07 ("What happens to my data if I cancel my account?") was clamped to `INSUFFICIENT_CONTEXT` even though `security-policy.md` + `acceptable-use.md` together arguably contain enough context. The grounded-prompt is currently bias-toward-refusal; loosening it without sacrificing groundedness needs a **two-stage retrieve → rerank** pass (Bedrock has a native reranker), or an explicit query-rewriter that splits compound questions into single-doc sub-questions before retrieval.
- **Confidence is retrieval-score-only.** The formula in [§4](#4-rag--agent-behavior) ignores whether the model actually cited the chunks it received. A **citation-coverage check** (parse the `[n]` tokens in the answer, verify each maps to a returned source, weight accordingly) would catch the case where confidence is high but the answer is ungrounded.

Beyond eval quality, the next priority is **CI/CD + per-user (`actor_id`) rate limiting + a CloudWatch budget alarm** — the three deferred items most likely to bite in a real production rollout. The full list is in [docs/HARDENING.md](docs/HARDENING.md).

---

## 11. Run instructions

Day-to-day loop (after the one-time setup in [docs/RUNBOOK.md](docs/RUNBOOK.md)):

```bash
cd infra && env -u PYTHONPATH cdk deploy --all --require-approval never && cd ..
./scripts/run_streamlit.sh
# → http://localhost:8501
```

The launcher reads live stack outputs from CloudFormation + Secrets Manager and rewrites `streamlit_client/.streamlit/secrets.toml` automatically. IDs that rotate on redeploy are picked up with no manual editing. Demo username: `demo`.

Full deploy / validate / tear-down / Streamlit Cloud deployment steps: **[docs/RUNBOOK.md](docs/RUNBOOK.md)**.

---

## 12. Repository layout

| Path | Purpose |
|---|---|
| [README.md](README.md) | This file. |
| [docs/](docs/) | [ARCHITECTURE](docs/ARCHITECTURE.md), [RUNBOOK](docs/RUNBOOK.md), [HARDENING](docs/HARDENING.md). |
| [PLAN.md](PLAN.md) | Original implementation plan (phases, decisions, risks). |
| [SESSION_HANDOFF.md](SESSION_HANDOFF.md) | Dense state-of-project doc, updated per phase. |
| [AWS Native Knowledge Base Agent Candidate Project Brief.md](AWS%20Native%20Knowledge%20Base%20Agent%20Candidate%20Project%20Brief.md) | Original take-home brief. |
| [sample-docs/](sample-docs/) | 6 Markdown bootstrap docs. |
| [infra/](infra/) | CDK app (4 stacks). See [infra/README.md](infra/README.md). |
| [lambda/](lambda/) | Buffered Lambda handler (RIC image). Routes 7 endpoints. |
| [lambda_stream/](lambda_stream/) | Streaming Lambda handler (LWA + FastAPI/uvicorn image). |
| [agent/](agent/) | AgentCore Runtime container (arm64). |
| [streamlit_client/](streamlit_client/) | Streamlit client (chat + sidebar + uploader). |
| [scripts/](scripts/) | Operator scripts. See [scripts/README.md](scripts/README.md). |
| [tests/](tests/) | Unit, synth, and eval tests. |
| [legacy/](legacy/) | Original Gemini + FAISS + Streamlit prototype, preserved for diff. |

---

## 13. Further reading

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — full ASCII diagram, request flow, 4-stack deep dive, complete API contract, AgentCore Runtime/Memory rationale, live-deploy gotchas.
- **[docs/RUNBOOK.md](docs/RUNBOOK.md)** — first-time deploy, two-command day-to-day loop, Streamlit Cloud production deployment, tear-down, cost summary.
- **[docs/HARDENING.md](docs/HARDENING.md)** — what's implemented in this submission vs. the deferred production roadmap.
- **[PLAN.md](PLAN.md)** — original implementation plan (canonical for the phase-by-phase build narrative).
- **[SESSION_HANDOFF.md](SESSION_HANDOFF.md)** — dense session-to-session state record (mostly for the AI agent reviewing this with me).
