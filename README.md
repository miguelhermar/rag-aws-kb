# RAG-AWS — Knowledge Base Agent on AWS

> **Status: live and reviewer-ready.** A local Streamlit client calls a CDK-deployed, authenticated AWS API that fronts a Bedrock Knowledge Base (S3 Vectors) and Anthropic Claude Haiku 4.5, and returns grounded answers with citations + confidence. Smoke test passes 4/4; eval surfaces 100% source-match on 6 in-corpus questions with documented limits on 1 ambiguous + 1 off-corpus case.

This repository is a productionization of a Streamlit + Gemini + FAISS prototype (preserved under [legacy/](legacy/)). It satisfies the AWS-Native Knowledge Base Agent take-home brief: CDK infrastructure, authenticated API, sample-seeded knowledge base, local Streamlit client, structured JSON responses with grounded citations, and a documented production path.

---

## 1. Architecture

```
┌──────────────────── LOCAL  (reviewer's machine) ─────────────────────────────────┐
│                                                                                  │
│   ┌──────────────────────┐   reads:  .streamlit/secrets.toml                     │
│   │  Streamlit chat UI   │            API_BASE_URL = https://…/prod/             │
│   │  streamlit_client/   │            API_KEY      = <32 alnum>                  │
│   │  app.py              │   never committed; sourced from Secrets Manager once  │
│   └──────────┬───────────┘                                                       │
│              │                                                                   │
│              │ (1) POST /query    headers: x-api-key: <API_KEY>                  │
│              │                    body:    {"question": "...", "top_k": 5}       │
└──────────────┼───────────────────────────────────────────────────────────────────┘
               │  HTTPS (TLS 1.2+)
               ▼
┌──────────────────────────── AWS   (us-east-1) ───────────────────────────────────┐
│                                                                                  │
│   ┌───────────────────────────────────────────┐  (2) verify x-api-key            │
│   │  API Gateway  (REST, regional)            │ ───► UsagePlan (5 req/s burst    │
│   │   GET   /health   (no auth)               │      10, 1000/day) ── 403 if     │
│   │   POST  /query    (x-api-key required)    │      missing/invalid             │
│   └──────────────────┬────────────────────────┘                                  │
│                      │ (3) AWS_PROXY integration                                 │
│                      ▼                                                           │
│   ┌──────────────────────────────────────────────┐                               │
│   │  Lambda  (container image, x86_64, 1024MB)   │ ──► CloudWatch Logs           │
│   │   ApiStack-RagHandler                        │     /aws/lambda/ApiStack-…    │
│   │   image: public.ecr.aws/lambda/python:3.12   │     30-day retention          │
│   │   env:   KB_ID, MODEL_ARN, MODEL_ID,         │     structured JSON +         │
│   │         LOG_LEVEL=INFO                       │     request_id on every line  │
│   │                                              │                               │
│   │  lambda/app.py                               │                               │
│   │   ├─ pydantic validate request → 400 if bad  │                               │
│   │   ├─ uuid4 request_id, perf_counter t0       │                               │
│   │   └─ rag.run_query(...)  ─────────────────┐  │                               │
│   └──────────────────────────────────────────┼──┘                                │
│                                              │                                   │
│              (4) bedrock-agent-runtime.Retrieve(kbId, query, top_k)              │
│                                              ▼                                   │
│   ┌────────────────────────────────────────────────────────────┐                 │
│   │  Bedrock Knowledge Base   (LA8DA5P7HH — rotates per deploy)│                 │
│   │   chunking:  FIXED_SIZE 300 tokens, 20% overlap            │                 │
│   │   embedder:  Amazon Titan Text Embeddings V2 (1024-dim)    │                 │
│   │   storage:   S3 Vectors (native, GA Jan 2026 — no OSS)     │                 │
│   │   metric:    cosine                                         │                │
│   └─────┬───────────────────────────────────────────┬──────────┘                 │
│         │  (5) returns top-k chunks                 │                            │
│         │      [{content, score, s3 location}, …]   │                            │
│         │                                           │ on ingest: embeds docs     │
│         ▼                                           ▼ writes vectors to index    │
│   ┌──────────────────┐                  ┌─────────────────────────────┐          │
│   │  S3  (docs)      │ ◄───── reads     │  S3 Vectors  (index)        │          │
│   │  6 sample-docs/  │                  │  rag-aws-kb-index           │          │
│   │  *.md files      │                  │  dim=1024  cosine  float32  │          │
│   └──────────────────┘                  └─────────────────────────────┘          │
│                                                                                  │
│              (6) bedrock-runtime.InvokeModel(MODEL_ARN, system+messages)         │
│                       MODEL_ARN = us.anthropic.claude-haiku-4-5-20251001-v1:0    │
│                                  (US cross-region inference profile)             │
│              ▼                                                                   │
│   ┌──────────────────────────────────────────────────────────────────┐           │
│   │  Bedrock Runtime → Claude Haiku 4.5                              │           │
│   │   system: "Answer ONLY from context. Cite as [n]. If insufficient│           │
│   │            reply EXACTLY `INSUFFICIENT_CONTEXT`."                │           │
│   │   user:   [1] <chunk>  [2] <chunk> …  Question: <user question>  │           │
│   └────────────────────┬─────────────────────────────────────────────┘           │
│                        │ (7) answer text                                         │
│                        ▼                                                         │
│   ┌──────────────────────────────────────────────────────┐                       │
│   │  rag.py response assembly                            │                       │
│   │    confidence = clamp(0.6·max + 0.3·mean             │                       │
│   │                       + 0.1·(1 − stdev), 0, 1)       │                       │
│   │    if answer startswith INSUFFICIENT_CONTEXT:        │                       │
│   │       answer = canned "I don't have information…"    │                       │
│   │       confidence = min(confidence, 0.2)              │                       │
│   └──────────────────┬───────────────────────────────────┘                       │
│                      │                                                           │
│                      ▼                                                           │
│       {"answer": …, "confidence": …, "sources": [{s3_uri, document,              │
│        score, snippet}, …], "metadata": {model, retrieval_strategy,              │
│        request_id, latency_ms}}                                                  │
└──────────────────┬───────────────────────────────────────────────────────────────┘
                   │ (8) 200 JSON (or 400/403/500 envelope)
                   ▼
            (returned through APIGW to the Streamlit client, rendered as
             markdown + colored confidence badge + expandable Sources)

──── Secret flow (out-of-band, one-time) ─────────────────────────────────────────
Secrets Manager  ── stores the API-key value, rotated by scripts/rotate_api_key.py
       │
       ├── at deploy time: ApiStack reads via CFN dynamic reference
       │      {{resolve:secretsmanager:<arn>:SecretString:apiKey}} → binds to
       │      APIGW ApiKey + UsagePlan
       │
       └── at setup time:  reviewer runs `aws secretsmanager get-secret-value …`
                           once and pastes the value into local secrets.toml
```

### 1.1 Architecture summary

A single user request flows: **Streamlit → APIGW (auth) → Lambda → Bedrock KB.Retrieve → Bedrock.InvokeModel → response composition → JSON back.** No third-party services are in the runtime path; every dependency is AWS-managed in `us-east-1`. Sample documents are ingested into the KB out-of-band via the operator script ([scripts/start_ingestion.py](scripts/start_ingestion.py)), not via a user-facing upload UI (deliberately out of scope per brief §4).

### 1.2 Key tradeoffs

| Decision | Chose | Over | Why |
|---|---|---|---|
| Vector store | **S3 Vectors** (native to Bedrock KB) | OpenSearch Serverless | Brief forbids OSS; S3 Vectors is the only GA, low-idle-cost AWS-managed vector option (GA Jan 2026). |
| Compute | **Lambda container image** (1024MB, 30s) | ECS/Fargate; zip Lambda | Smallest blast radius + cheapest idle for a query-only API. Container chosen over zip so pydantic + boto3 + structlog fit comfortably and the same image runs locally for debugging. |
| LLM | **Claude Haiku 4.5** via cross-region inference profile | Sonnet; Claude 3 Haiku | Brief recommended Claude 3 Haiku, but it became LEGACY post-2026 and Marketplace-gated. Haiku 4.5 is the modern equivalent. Inference profile gives multi-region failover for free. |
| Embeddings | **Titan v2 (1024-dim)** | Titan v1; Cohere | Required by S3 Vectors + KB; 1024 dims is the smallest Titan v2 option and keeps index storage cheap. |
| Auth | **API Gateway API key + UsagePlan**, key from Secrets Manager | Cognito JWT; Lambda authorizer | Brief explicitly permits API key for a "lightweight demo". Production identity model (Cognito) is documented in §10. |
| AgentCore | **Not used** | AgentCore Runtime | Considered. AgentCore adds runtime memory, gateway, observability — valuable when you have multiple tools/agents. For a single-turn RAG with one retrieval tool and no memory, it adds operational surface without product value. Documented as a future direction in §10. |
| RAG strategy | **`Retrieve` + `InvokeModel` separately** | `RetrieveAndGenerate` | Gives us full prompt control (the INSUFFICIENT_CONTEXT contract requires it) and a clean schema for the `sources[]` field. |
| Chunking | **FIXED_SIZE 300 tokens, 20% overlap** | Semantic chunking; hierarchical | Sample docs are short policy markdown; fixed chunking is predictable, debuggable, and adequate for the corpus. |
| IaC | **AWS CDK in Python** | Terraform; SAM | Brief mandate. Two stacks (Storage / Api) so the slow-to-create KB resources aren't rebuilt every time the Lambda image changes. |

---

## 2. AWS services + CDK stacks

| Service | What it does in this app | CDK construct |
|---|---|---|
| **API Gateway** (REST, regional) | Public HTTPS entry point; enforces `x-api-key`; routes `/health`+`/query` to Lambda; emits 4xx/5xx + latency metrics. | `aws_apigateway.RestApi` |
| **AWS Lambda** (container image) | Validates request, calls KB Retrieve + Bedrock InvokeModel, composes response. 1024MB / 30s / x86_64, 1 concurrent. | `aws_lambda.DockerImageFunction` |
| **Amazon ECR** | Hosts the Lambda container image (auto-created by CDK from `lambda/Dockerfile`). | (implicit via `DockerImageAsset`) |
| **Bedrock Knowledge Base** | Chunks + embeds docs, indexes vectors, serves `Retrieve` queries. | `aws_bedrock.CfnKnowledgeBase` (L1) |
| **Bedrock Runtime** (Claude Haiku 4.5) | Generates the grounded answer from the prompt. Invoked via inference profile for multi-region availability. | (not provisioned — IAM-granted access) |
| **Amazon S3** (docs bucket) | Stores the 6 `sample-docs/*.md` files that seed the KB. `autoDeleteObjects=True` for clean teardown. | `aws_s3.Bucket` |
| **S3 Vectors** (index) | Native AWS vector store backing the KB. 1024-dim, cosine, float32. | `aws_s3vectors.CfnVectorBucket` + `CfnIndex` |
| **Secrets Manager** | Holds the API key (random 32-char alnum). Read by CFN dynamic reference at deploy time; rotated by operator script. | `aws_secretsmanager.Secret` |
| **CloudWatch Logs** | Structured JSON logs from Lambda; 30-day retention. Every log line carries the `request_id`. | `aws_logs.LogGroup` |
| **IAM** (least-privilege) | KB role: `s3:GetObject` on docs bucket only, `s3vectors:*` on the one index ARN, `bedrock:InvokeModel` on Titan v2 ARN. Lambda role: `bedrock:Retrieve` on KB ARN, `bedrock:InvokeModel` on Haiku 4.5 inference profile + the 3 foundation-model ARNs the profile fans to. | inline policies |

**Two stacks, one CDK app** ([infra/app.py](infra/app.py)):

- **StorageStack** ([infra/stacks/storage_stack.py](infra/stacks/storage_stack.py)) — long-lived data plane: docs bucket, S3 Vectors bucket+index, Bedrock KB + data source, Secrets Manager secret, KB IAM role. Deploys in ~60s.
- **ApiStack** ([infra/stacks/api_stack.py](infra/stacks/api_stack.py)) — query plane: Lambda container function, log group, APIGW, ApiKey, UsagePlan. Depends on StorageStack (cross-stack reference, in-memory not via `Fn.import_value`). Deploys in ~70s (~3min cold, ~70s with warm docker cache).

Outputs from both stacks are written to [cdk-outputs.json](cdk-outputs.json) (gitignored) and consumed by all operator scripts via `jq`.

---

## 3. API contract & authentication

### 3.1 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET`  | `/health` | none | Liveness probe; no AWS calls. |
| `POST` | `/query`  | `x-api-key` header | Grounded RAG answer. |

### 3.2 Request

```json
{ "question": "What is the refund window for monthly plans?", "top_k": 3 }
```
- `question` (string, required, min length 1) — natural-language question.
- `top_k` (int, optional, default 5, range 1–20) — how many chunks to retrieve.

(We do not support `session_id` from the brief example — the API is stateless. See [§9 Assumptions](#9-assumptions-known-limitations--data-flow).)

### 3.3 Successful response (200)

```json
{
  "answer": "According to the Acme Notes Refund Policy, the refund window for monthly plans is **30 days** of the most recent charge… [1]",
  "confidence": 0.672,
  "sources": [
    {
      "s3_uri":   "s3://storagestack-docsbucketecea003f-…/refund-policy.md",
      "document": "refund-policy.md",
      "score":    0.657,
      "snippet":  "Refunds are available within 30 days…"
    }
  ],
  "metadata": {
    "model":              "anthropic.claude-haiku-4-5-20251001-v1:0",
    "retrieval_strategy": "bedrock-kb-s3vectors-titan-v2-topk",
    "request_id":         "11111111-2222-3333-4444-555555555555",
    "latency_ms":         1913
  }
}
```

Schema deviations from the brief's §6 example (intentional, documented):
- `document_id` → `document` (filename only; the full S3 URI lives in `s3_uri`).
- `chunk_id` → omitted (S3 Vectors does not expose stable chunk IDs across re-ingestions; the snippet + s3_uri are stable and sufficient for human verification).
- `excerpt` → `snippet` (semantically identical; truncated to 240 chars).

### 3.4 Error envelope

```json
{ "error": "InvalidRequest", "message": "…pydantic detail…", "request_id": "…" }
```

| Status | `error`           | When                                                                 |
|------:|-------------------|----------------------------------------------------------------------|
| 400   | `InvalidRequest`  | Malformed JSON; missing/empty `question`; `top_k` out of range.      |
| 403   | (APIGW message)   | Missing or invalid `x-api-key`. Enforced by APIGW before Lambda runs.|
| 500   | `Internal`        | Bedrock `ClientError` or any unhandled exception. Generic message — full traceback in CloudWatch keyed by `request_id`. Never leaks internals. |

### 3.5 Authentication flow

- **How the token is created**: CDK provisions a Secrets Manager secret whose value is generated server-side as a JSON object `{"apiKey": "<32 random alnum>"}`. The value never lands in the CFN template (CFN dynamic reference `{{resolve:secretsmanager:<arn>:SecretString:apiKey}}` is resolved at the AWS control-plane side, not in our synth output).
- **Where it is stored**: in Secrets Manager (encrypted at rest with AWS-managed key). The APIGW `ApiKey` resource holds the same value internally; the two are bound via UsagePlan.
- **How the local Streamlit app passes it**: the reviewer fetches the value once via `aws secretsmanager get-secret-value` and pastes it into `streamlit_client/.streamlit/secrets.toml` (gitignored). Streamlit reads it via `st.secrets["API_KEY"]` and sends it as the `x-api-key` HTTP header on every `/query`.
- **How unauthorized calls are rejected**: APIGW rejects missing/invalid keys with `403 Forbidden` *before* the Lambda is invoked — no compute spend on unauthorized traffic.
- **Rotation**: `python scripts/rotate_api_key.py` generates a new value and writes it to Secrets Manager. Because the APIGW ApiKey resolves the secret only at deploy time, a follow-up `cdk deploy ApiStack` (with a description bump on the ApiKey construct) is required to propagate the new value. The script prints the recipe — full rationale in [scripts/README.md](scripts/README.md).
- **Production identity**: would replace API-key with Cognito JWT (per-user identity, MFA support, per-identity rate limits via APIGW custom authorizer). See [§10 Production hardening](#10-production-hardening-what-wed-do-next).

---

## 4. RAG behavior

### 4.1 Ingestion (out-of-band, operator-driven)

1. `scripts/upload_docs.py` syncs `sample-docs/*.md` to the docs S3 bucket (MD5-compared; idempotent).
2. `scripts/start_ingestion.py` calls `bedrock-agent.StartIngestionJob` and polls every 10s. The KB chunks each doc using **FIXED_SIZE 300 tokens with 20% overlap**, embeds each chunk with **Titan Text Embeddings V2 (1024-dim)**, and writes the vectors to the S3 Vectors index. Typical wall-clock: **~10s for 6 docs**.

A full event-driven ingestion pipeline (S3 PutObject → EventBridge → Step Function → StartIngestionJob) is documented as a Phase-7 hardening note but intentionally out of scope (brief §4: ingestion is optional).

### 4.2 Retrieval

For each request, `lambda/rag.py::retrieve()` calls `bedrock-agent-runtime.Retrieve` with `numberOfResults = top_k`. The KB returns up to `top_k` chunks (`content`, `score`, `location.s3Location.uri`); we extract just the filename for the `document` field.

### 4.3 Prompt construction & grounding

System prompt (verbatim, from [lambda/rag.py:12-17](lambda/rag.py#L12-L17)):

> Answer ONLY from the provided context. If the context is insufficient to answer the question, your entire response MUST be the single token `INSUFFICIENT_CONTEXT` with no other characters, words, or explanation. When you can answer, cite sources inline as [n].

User message: numbered chunks (`[1] …  [2] …`) followed by `Question: <user question>`.

The model is invoked via `bedrock-runtime.InvokeModel` against the **US cross-region inference profile** `us.anthropic.claude-haiku-4-5-20251001-v1:0`. The profile transparently fans out across us-east-1, us-east-2, us-west-2 for availability and throughput.

### 4.4 Confidence

```
confidence = clamp(0.6·max(scores) + 0.3·mean(scores) + 0.1·(1 − stdev(scores)), 0, 1)
```

Rationale: top-match score dominates (60%) because high-similarity hits are the strongest signal of relevance; mean (30%) rewards corpora that consistently match; stdev penalty (10%) prefers tight clusters over a single outlier hit. Formula is a simplified port of the legacy prototype's [legacy/rag/generator.py:217-264](legacy/rag/generator.py#L217-L264) (keyword boosts and length penalties removed — they were FAISS-specific and added noise without consistent value).

### 4.5 INSUFFICIENT_ANSWER override

If the model returns a string that starts with `INSUFFICIENT_CONTEXT` (we accept startswith rather than exact equality, because Haiku 4.5 sometimes elaborates after the token — discovered in Phase 5), the response is replaced with the canned `"I don't have information about that in the knowledge base."` and confidence is clamped to ≤ 0.2. This makes off-corpus and low-confidence answers obvious to the UI's badge colorer.

---

## 5. Sample documents

The KB is seeded with 6 Markdown documents under [sample-docs/](sample-docs/), each 3–4 KB, all under a fictional brand **Acme Notes** (collaborative-note SaaS, domain `acmenotes.example`):

| File | Topic |
|---|---|
| [refund-policy.md](sample-docs/refund-policy.md) | Refund windows, eligibility, exclusions. |
| [shipping-policy.md](sample-docs/shipping-policy.md) | Notebook shipping rates + timing (US + intl). |
| [security-policy.md](sample-docs/security-policy.md) | Authentication, encryption, incident response. |
| [employee-handbook.md](sample-docs/employee-handbook.md) | PTO, benefits, work expectations. |
| [product-faq.md](sample-docs/product-faq.md) | Plan comparison, feature availability. |
| [acceptable-use.md](sample-docs/acceptable-use.md) | Prohibited content + behavior. |

These were generated by Claude (the AI tool used for the build — see [§8](#8-ai-tools-used-during-development)) under a tight brief: realistic enterprise-policy tone, ~3 KB each, footer `*Last updated: 2026-05-01*`, no PII, no real-brand collisions. The corpus is intentionally small and topically diverse so retrieval behavior is easy to reason about during evaluation.

---

## 6. Demo evidence — one full live run

Below is an actual request/response captured against the live deployment (`https://id04zftvx5.execute-api.us-east-1.amazonaws.com/prod/`) on 2026-05-24 evening.

**Request** (curl-from-Streamlit equivalent):

```bash
curl -X POST "${API_URL}query" \
  -H "x-api-key: ${API_KEY}" -H "Content-Type: application/json" \
  -d '{"question":"What is the refund window for monthly plans?","top_k":3}'
```

**Response** (200, truncated `snippet` fields):

```json
{
  "answer": "According to the Acme Notes Refund Policy, the refund window for monthly plans is **30 days** of the most recent charge, provided the account has not exceeded fair-use thresholds during the billing period [1].",
  "confidence": 0.672,
  "sources": [
    {"s3_uri": "s3://…/refund-policy.md",  "document": "refund-policy.md",  "score": 0.657, "snippet": "Customers on monthly billing may request a full refund within 30 days…"},
    {"s3_uri": "s3://…/refund-policy.md",  "document": "refund-policy.md",  "score": 0.612, "snippet": "Refund requests are reviewed within 5 business days…"},
    {"s3_uri": "s3://…/product-faq.md",    "document": "product-faq.md",    "score": 0.541, "snippet": "Billing FAQ: when can I get my money back?…"}
  ],
  "metadata": {
    "model": "anthropic.claude-haiku-4-5-20251001-v1:0",
    "retrieval_strategy": "bedrock-kb-s3vectors-titan-v2-topk",
    "request_id": "9792a230-e2c7-4399-be31-cbc4678a6b56",
    "latency_ms": 1913
  }
}
```

**In the Streamlit UI**: the answer renders as markdown, a **green Confidence: 0.67** badge appears below it, and an expandable "Sources (3)" section lists each chunk with document, S3 URI, score, and snippet.

**In CloudWatch Logs** (`/aws/lambda/ApiStack-RagHandler`), every request emits structured JSON keyed by `request_id`:
```
{"event": "request.received", "request_id": "9792a230-…", "method": "POST", "path": "/prod/query", "timestamp": "2026-05-24T22:14:08Z"}
{"event": "request.success",  "request_id": "9792a230-…", "latency_ms": 1913, "sources": 3}
```

---

## 7. Evaluation

The full eval table + aggregate metrics are committed at [tests/eval/eval_results.md](tests/eval/eval_results.md) and reproducible via `python tests/eval/run_eval.py`. Question set: [tests/eval/questions.json](tests/eval/questions.json) — 8 questions covering all 6 sample docs + 1 ambiguous + 1 off-corpus.

### 7.1 Aggregate metrics (last live run)

| Metric | Value | Pass criterion |
|---|---:|---|
| Source-match rate (in-corpus, 6 Qs) | **100.0%** (6/6) | ≥ 80% |
| Mean confidence (in-corpus) | **0.813** | (informational) |
| Mean confidence (off-corpus) | **0.200** (clamped) | ≤ 0.2 |
| Total wall-clock (8 Qs, ~250ms pacing) | 20.2 s | — |
| Mean API latency | ~2.3 s/Q | — |

### 7.2 Good examples

- **q01** *"What is the refund window for monthly plans?"* → 30-day answer grounded in `refund-policy.md`, confidence 0.67.
- **q03** *"What authentication requirements does Acme Notes enforce…?"* → multi-paragraph answer grounded in `security-policy.md`, confidence 0.87 (high because the question wording closely mirrors source-doc headings).
- **q06** *"What kinds of content are prohibited under the acceptable use policy?"* → top score 0.94 (near-exact phrasing in the source); confidence 0.90.

### 7.3 Weak / ambiguous examples (where the brief asks us to be honest)

1. **q07 — ambiguous, spans multiple docs**: *"What happens to my data if I cancel my account?"* The model retrieved chunks (top hit `acceptable-use.md`, score 0.577) but judged context insufficient and returned the canned `INSUFFICIENT_ANSWER` + 0.2 confidence. Diagnosis: the question implicitly asks about *deletion timelines* + *export rights* + *refund implications*, which are spread across `security-policy.md`, `acceptable-use.md`, and `refund-policy.md`. Single-pass retrieval surfaces the closest single match, not a *synthesis* across docs. **Fix path** documented in §10 hardening: a query-rewrite step ("expand multi-faceted questions into sub-queries") or a `RetrieveAndGenerate`-style two-stage pipeline.
2. **q08 — off-corpus**: *"What is the office WiFi password?"* Correctly returned the canned answer at 0.2 confidence. Worth noting because it is a **design success, not a failure**: the prompt's strict grounding contract + the INSUFFICIENT_ANSWER override prevent hallucination on questions the corpus genuinely cannot answer. The system would have looked *more* confident with a less strict prompt — and been wrong.

### 7.4 Reflection — what we'd improve next

1. **Expand the eval set** to 30+ questions, including paraphrases of in-corpus questions, adversarial framings, and questions that genuinely require multi-doc synthesis (to systematically measure the q07 class of failure).
2. **Add a re-ranking step** (Bedrock supports rerank models) — cheap latency tax, big precision lift on borderline retrievals.
3. **Track per-source recall** and a `negative_response_rate` metric so regressions in prompt or model are detectable.
4. **Capture the question text + retrieved s3_uris** in CloudWatch (currently we log counts only, not bodies, to avoid persisting user input). PII redaction would need to be added if this is enabled in production.

---

## 8. Changes from the sample project (what we replaced & why)

The legacy prototype lives at [legacy/](legacy/). Side-by-side:

| Component | Sample (legacy/) | This solution | Why we changed it |
|---|---|---|---|
| Frontend | Streamlit with document upload + chat | Streamlit chat-only client | Brief §4 says upload is optional; we kept the client lean and pushed ingestion to an operator script. |
| API layer | FastAPI in-process | API Gateway + Lambda | Brief §4 requires an authenticated AWS-hosted API. APIGW gives free TLS, auth, throttling, metrics. |
| LLM | Google Gemini (third-party) | Anthropic Claude Haiku 4.5 via Bedrock | Brief mandates Bedrock + Anthropic; also keeps the data plane AWS-only (no third-party hops). |
| Embeddings | local (sentence-transformers) | Amazon Titan v2 via Bedrock KB | Required by S3 Vectors + KB; managed, no embedding-model ops. |
| Vector store | FAISS (in-process, ephemeral) | Bedrock KB on **S3 Vectors** | Persistent, managed, native to AWS; no idle compute (vs. OpenSearch Serverless which the brief forbids and which has high idle cost). |
| Document loaders | custom PDF/MD loaders | KB ingestion job | Less code, managed chunking. (We restricted to MD-only for the sample corpus; KB supports PDF/HTML/TXT for prod expansion.) |
| Confidence | FAISS-score heuristic + keyword boost + length penalty | Simplified `0.6·max + 0.3·mean + 0.1·(1 − stdev)` | Removed heuristics that were FAISS-specific and noisy in practice; the simpler formula correlates well with answer quality in our eval. |
| Auth | none (local-only) | APIGW API key + UsagePlan, Secrets Manager-sourced | Brief §7. |
| Deployment | `docker-compose up` (local) | AWS CDK (Python), two stacks | Brief §8. |
| Observability | print/log | CloudWatch structured JSON + request_id correlation + APIGW metrics | Brief §12 — production thinking. |
| Secrets | `.env` in repo (Gemini key) | Secrets Manager + `.gitignore`'d `secrets.toml` | Brief §7 (no secrets in repo). The legacy `.env` is preserved on disk for diff but never pushed; the new stack does not use Gemini at all. |

---

## 9. AI tools used during development

This project was built using **Claude Code** (Anthropic's CLI) running Opus 4.7 as the orchestrating agent, with `general-purpose` sub-agents handling chunked phases (CDK authoring, Lambda authoring, operator-script authoring, Streamlit+eval authoring). Pattern, applied per phase:

1. The orchestrator wrote a tight per-phase brief grounded in [PLAN.md](PLAN.md) + the current handoff doc.
2. A sub-agent did the implementation against the brief, with explicit instructions to verify external APIs (AWS docs, CDK API reference, Streamlit docs) via `WebFetch` *before* generating code — to compensate for training-cutoff drift.
3. The orchestrator trust-but-verified: read all new files, re-ran tests + smoke + (where applicable) live `curl` smoke against the deployed API, and confirmed `cdk diff` showed no IaC drift.
4. Each phase committed atomically with the handoff doc updated, so a fresh Claude session can resume cold (see [SESSION_HANDOFF.md](SESSION_HANDOFF.md)).

**Two incidents are documented in the handoff** ([§14](SESSION_HANDOFF.md)) and shaped the patterns above:
- An "out-of-band AWS change" incident — a sub-agent edited live Lambda config via CLI instead of through CDK source. Now every infra-touching sub-agent is explicitly briefed to never mutate live AWS outside CDK, and `cdk diff` is a phase-completion gate.
- A model-deprecation incident — Claude 3 Haiku (the brief's recommendation) became LEGACY post-2026. We re-locked to Haiku 4.5 via inference profile.

Sample documents (§5) were generated by Claude with a single-paragraph brief; the prompt is reproducible from `git log -- sample-docs/`.

---

## 10. Production hardening (what we'd do next)

Implemented in this submission, in order of how much they'd cost to retrofit later:

- **Least-privilege IAM** on KB + Lambda roles (specific ARNs, no wildcards).
- **Structured JSON logs** with per-request `request_id` correlation across Lambda + APIGW.
- **Schema validation** (pydantic v2) at the request boundary; never leak exception detail to clients.
- **30-day log retention** + **APIGW UsagePlan quotas** (1000/day, 5/s burst 10) to cap blast radius and spend.
- **Secrets via Secrets Manager** + CFN dynamic references — no secret values in synth output, never logged.
- **`autoDeleteObjects=True`** on the docs bucket + `RemovalPolicy.DESTROY` on most resources for clean tear-down (brief §8).

Documented but **not** implemented (the "what we'd do for production" list — brief §4.5):

- **Identity**: Cognito JWT in place of API key; per-user rate limits via APIGW custom authorizer; MFA enforcement.
- **Network**: VPC + interface endpoints for Bedrock, Secrets Manager, S3; Lambda in private subnets; AWS WAF on APIGW (rate, geo, OWASP common rules).
- **Encryption**: KMS CMKs on docs bucket, Secrets Manager secret, CloudWatch Logs.
- **Observability**: X-Ray tracing through APIGW + Lambda + Bedrock; CloudWatch budget alarms at $10/$18; per-question + per-user dashboards.
- **Reliability**: Bedrock model fallback (Haiku → Sonnet with backoff); SnapStart for container Lambdas when GA; dead-letter queue for failed invocations.
- **Document lifecycle**: event-driven ingestion (S3 PutObject → EventBridge → Step Function → StartIngestionJob) with backoff + alerting; document-deletion sync to KB; periodic re-ingestion for embedding-model upgrades.
- **PII**: output redaction before logging; input PII scan + reject on `/query`.
- **RAG quality**: query-rewriting for ambiguous questions (q07 class); reranking; per-source recall + groundedness eval gate in CI.
- **AgentCore**: would consider Bedrock AgentCore Runtime if the product evolves to multi-tool agents (e.g., add "search ticket history" or "execute SQL against a metrics DB" tools) — its memory + gateway + observability features earn their keep at that point.

---

## 11. Run instructions

Prereqs: AWS CLI configured, account with Bedrock access to Titan v2 + Haiku 4.5 inference profile enabled in `us-east-1`, Docker (for Lambda image build), Python 3.12, Node.js (for CDK CLI).

### 11.1 Deploy (~3 min cold, ~70s if docker layers warm)

```bash
# One-time bootstrap (per account/region)
cd infra && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
env -u PYTHONPATH cdk bootstrap aws://<ACCOUNT_ID>/us-east-1

# Deploy both stacks
env -u PYTHONPATH cdk deploy StorageStack ApiStack --require-approval never \
  --outputs-file ../cdk-outputs.json
```

(The `env -u PYTHONPATH` is a defense against globally-set `PYTHONPATH` shadowing the `constructs` pypi package.)

### 11.2 Seed the knowledge base (~15s)

```bash
cd .. && source venv/bin/activate
python scripts/upload_docs.py       # MD5-idempotent upload of sample-docs/
python scripts/start_ingestion.py   # polls until COMPLETE; ~10s for 6 docs
```

### 11.3 Validate (~10s)

```bash
python scripts/smoke_test.py        # exits 0 iff health=200, /query=200+schema, no-key=403, empty=400
python tests/eval/run_eval.py       # writes tests/eval/eval_results.md (8 questions, ~20s)
```

### 11.4 Run the local Streamlit client

```bash
cp streamlit_client/.streamlit/secrets.toml.example streamlit_client/.streamlit/secrets.toml
# Edit secrets.toml: API_BASE_URL from cdk-outputs.json, API_KEY from:
SECRET_ARN=$(jq -r '.StorageStack.ApiKeySecretArn' cdk-outputs.json)
aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --region us-east-1 \
  --query SecretString --output text | jq -r .apiKey

./venv/bin/python -m streamlit run streamlit_client/app.py
# → http://localhost:8501  (chat UI; ask "What is the refund window?")
```

### 11.5 Tear down (no ongoing cost)

```bash
cd infra && source .venv/bin/activate
env -u PYTHONPATH cdk destroy ApiStack StorageStack --force
# Lambda's auto-created ECR repo may linger — confirm with `aws ecr describe-repositories`
# and `aws ecr delete-repository --force --repository-name cdk-...` if you want full zero.
```

**Cost**: idle ≈ **$0.40/month** (Secrets Manager flat fee + a few MB of ECR image storage). Active ≈ **$0.0003 per /query** (Haiku tokens dominate; APIGW + Lambda compute are rounding error). Our cumulative project spend was under **$1** over 6 phases.

---

## 12. Repository layout

| Path | Purpose |
|---|---|
| [README.md](README.md) | This file. |
| [PLAN.md](PLAN.md) | Implementation plan (phases, decisions, risks). |
| [SESSION_HANDOFF.md](SESSION_HANDOFF.md) | Dense state-of-the-project doc, updated per phase. |
| [AWS Native Knowledge Base Agent Candidate Project Brief.md](AWS%20Native%20Knowledge%20Base%20Agent%20Candidate%20Project%20Brief.md) | The original brief. |
| [sample-docs/](sample-docs/) | 6 Markdown docs that seed the knowledge base. |
| [infra/](infra/) | CDK app (Python). `stacks/{storage_stack,api_stack}.py` + `constructs/{s3_vectors,bedrock_kb}.py`. |
| [lambda/](lambda/) | Container-based Lambda handler. `app.py` (routing), `rag.py` (Retrieve+Invoke+confidence), `schemas.py` (pydantic). |
| [streamlit_client/](streamlit_client/) | Local Streamlit chat client. |
| [scripts/](scripts/) | `upload_docs.py`, `start_ingestion.py`, `rotate_api_key.py`, `smoke_test.py`. |
| [tests/](tests/) | `test_schemas.py` + `test_rag.py` (24 unit tests, botocore Stubber, no AWS), `test_synth.py` (5 CDK synth-time assertions), `eval/` (question set + harness + committed results). |
| [legacy/](legacy/) | Original Gemini + FAISS + Streamlit prototype, preserved for diff. See [legacy/README.md](legacy/README.md). |

---

## 13. Assumptions, known limitations & data flow

### 13.1 Assumptions

- AWS account `954863244564` in `us-east-1`. Region is hard-pinned in [infra/app.py](infra/app.py) because S3 Vectors GA regions are limited (us-east-1, us-east-2, us-west-2, eu-west-1, ap-southeast-2 at time of writing).
- Bedrock model access for Titan v2 + the Claude Haiku 4.5 inference profile is enabled in the account (one-time manual console step, since console-acceptance of model terms can't be IaC'd).
- Reviewer has Docker available locally for the Lambda container image build during `cdk deploy`.
- The corpus is the 6 sample-docs files. The system supports any KB-compatible format (PDF, HTML, TXT) — only the corpus is restricted.

### 13.2 Known limitations

- **Stateless API** — no `session_id`, no conversation memory. Multi-turn dialog would need either Cognito + DynamoDB session state, or Bedrock AgentCore memory.
- **Ambiguous, multi-doc questions** can be incorrectly flagged INSUFFICIENT_ANSWER (eval q07). Mitigations are documented in [§10](#10-production-hardening-what-wed-do-next).
- **No streaming** — `/query` returns the full response when the model finishes. Streaming would require APIGW WebSocket or response-streaming Lambda; both are available but add operational complexity not justified for the demo.
- **API key rotation requires a stack redeploy** (see [§3.5](#35-authentication-flow)) — a real product would move to Cognito JWT.
- **`venv/` at repo root** has stale `RAG-demo/` shebangs from the legacy directory rename; affects only the operator running scripts directly via `venv/bin/python -m <module>` (works) vs. `venv/bin/python script.py` (works) vs. `venv/bin/streamlit` (broken — use `venv/bin/python -m streamlit` instead). Trivially fixable by recreating the venv.

### 13.3 Data flow & external services

**No data leaves AWS in the runtime path.** The full request → response cycle stays inside AWS APIs (APIGW, Lambda, Bedrock KB, Bedrock Runtime, S3, S3 Vectors, CloudWatch, Secrets Manager). The only "external" hop is the local Streamlit client → APIGW (HTTPS / TLS 1.2+), which is the entry to the AWS boundary.

The single secret that crosses the AWS boundary is the API key, delivered to the local `secrets.toml` once via `aws secretsmanager get-secret-value` (an AWS API call). It never appears in source, logs, or telemetry.

The user's question text is logged to CloudWatch only via metadata (`event`, `request_id`, `latency_ms`, `sources count`) — **not the question body or the answer body**, to avoid persisting potentially-sensitive input. This is a deliberate observability/PII tradeoff; production would add structured (and redacted) request/response logging behind a feature flag.

---

## 14. Testing & validation

| What | How | Where |
|---|---|---|
| Schema round-trip + validation | 14 pytest cases | [tests/test_schemas.py](tests/test_schemas.py) |
| RAG logic (retrieve, confidence, INSUFFICIENT) | 9 pytest cases with botocore `Stubber` (no AWS calls) | [tests/test_rag.py](tests/test_rag.py) |
| CDK synth-time invariants | 5 pytest cases (single Lambda, single RestApi, env contains `KB_ID`+`MODEL_ARN`, `/query` requires key, `/health` does not) | [tests/test_synth.py](tests/test_synth.py) |
| Live API contract + auth + happy path + empty-request | 4-check smoke test, exit-0 contract | [scripts/smoke_test.py](scripts/smoke_test.py) |
| End-to-end RAG quality | 8-question eval harness, writes markdown report | [tests/eval/run_eval.py](tests/eval/run_eval.py) |
| IaC drift | `cdk diff` (must show 0 differences at end of every phase) | manual |

`source venv/bin/activate && python -m pytest tests/test_schemas.py tests/test_rag.py -v` runs in 0.2 s and covers everything that doesn't need AWS. Synth tests need the `infra/.venv` and a synthesized stack.

---

## 15. Closing

The brief asks for a "small, clean, well-explained implementation rather than a large unfinished one." This submission lives by that — narrow scope (single-turn RAG), strong primitives (Bedrock KB on S3 Vectors, Lambda container, APIGW key auth), production-thinking documented in §10 rather than half-implemented, and an explicit honest accounting of the one weak case in §7.3.

The full session-by-session build log lives in [SESSION_HANDOFF.md](SESSION_HANDOFF.md) for anyone who wants to see how the project evolved. The full implementation plan with phase-by-phase decisions is in [PLAN.md](PLAN.md).

**Live as of the last commit**:
- API: `https://id04zftvx5.execute-api.us-east-1.amazonaws.com/prod/` (rotates per redeploy — read from `cdk-outputs.json`)
- KB: `LA8DA5P7HH` (rotates)
- Smoke: 4/4, eval source-match 100% in-corpus.

Run `python scripts/smoke_test.py` to verify the same end-to-end on your shell.
