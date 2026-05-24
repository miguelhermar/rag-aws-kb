# RAG-AWS Productionization — Implementation Plan

## Context

The repo is a working RAG prototype: a Streamlit app ([app.py](app.py), 1362 LOC) that does in-process Google Gemini embeddings + FAISS retrieval + Gemini generation, plus a partial FastAPI/Next.js stack under [backend/](backend/) and [frontend/](frontend/). It is single-user, has no IaC, no auth, and ships a leaked Gemini key in `.env`. The brief ([AWS Native Knowledge Base Agent Candidate Project Brief.md](AWS%20Native%20Knowledge%20Base%20Agent%20Candidate%20Project%20Brief.md)) asks us to take this prototype and deliver an **AWS-native, CDK-deployed, authenticated, source-grounded RAG service** that a local Streamlit client calls over HTTPS — under a **$20 hard budget**, with no OpenSearch Serverless and no SageMaker.

The intended outcome: a reviewer clones the repo, runs `cdk deploy`, runs two operator scripts, starts a local Streamlit, asks a question, and gets a grounded answer with sources and confidence — with code, IaC, README, tests, and an evaluation harness all included.

## Locked decisions

| Area | Choice | Why |
|---|---|---|
| Compute | **Lambda** (container image, 1024 MB, 30 s) | Free-tier-friendly; honors $20 cap. Fargate path documented for production. |
| Retrieval | **Bedrock Knowledge Base** backed by **S3 Vectors** (GA Jan 2026) | AWS-native, no OSS/Aurora/Pinecone, lowest cost. |
| LLM | **Claude Haiku 4.5** via Bedrock cross-region inference profile (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) | Original lock was Claude 3 Haiku (`anthropic.claude-3-haiku-20240307-v1:0`), but it was deprecated to LEGACY post-2026 and now requires an AWS Marketplace subscription. Haiku 4.5 is the current-gen Haiku tier (cheap, fast), no Marketplace gate. Re-locked 2026-05-24. |
| Embeddings | **Titan Text Embeddings V2** (`amazon.titan-embed-text-v2:0`) | Required by S3 Vectors / KB integration. |
| Auth | **API Gateway API key + Usage Plan** | Simplest path that meets brief; key sourced from Secrets Manager. |
| IaC | **AWS CDK in Python** | Per brief. |
| Region | **us-east-1** | S3 Vectors GA + widest Bedrock model availability. |
| AgentCore | Out of scope (README only) | Single-tool RAG doesn't justify it at this budget. |
| Legacy code | **Move to [legacy/](legacy/)** | Preserves diff for "changes from sample" narrative without polluting root. |

## Target architecture

```
LOCAL                                AWS (us-east-1)
┌───────────────┐    HTTPS         ┌──────────────────────────┐
│ Streamlit     │ ───────────────► │ API Gateway (REST)       │
│ streamlit_    │  x-api-key       │  POST /query             │
│ client/app.py │                  │  GET  /health            │
└───────────────┘                  │  UsagePlan: 2 rps, 200/d │
                                   └────────────┬─────────────┘
                                                │ AWS_PROXY
                                                ▼
                                   ┌──────────────────────────┐
                                   │ Lambda (container, py)   │
                                   │  IAM: bedrock:Retrieve   │
                                   │       bedrock:Invoke     │
                                   │       (haiku ARN only)   │
                                   └──┬───────────────────┬───┘
                              Retrieve│                   │InvokeModel
                                      ▼                   ▼
                            ┌──────────────────┐ ┌──────────────────┐
                            │ Bedrock KB       │ │ Bedrock Runtime  │
                            │ Titan v2 embed   │ │ Claude 3 Haiku   │
                            │ S3 Vectors store │ └──────────────────┘
                            └────┬─────────────┘
                                 │
                    ┌────────────┴──────────────┐
                    ▼                           ▼
         ┌──────────────────┐         ┌────────────────────┐
         │ S3 Vector Bucket │◄────────│ S3 Docs Bucket     │
         │ (vectors + meta) │  ingest │ sample-docs/*.md   │
         └──────────────────┘  job    └────────────────────┘

         Secrets Manager: API key value (read by CDK → APIGW ApiKey)
         CloudWatch: Lambda + API GW access logs (7-day retention)
```

## Repo layout (post-change)

```
infra/                          # CDK app (new)
  app.py
  cdk.json
  requirements.txt
  stacks/
    storage_stack.py            # S3 docs, S3 Vectors, KB, secret
    api_stack.py                # Lambda, API GW, usage plan
  constructs/
    s3_vectors.py               # AwsCustomResource wrapper
    bedrock_kb.py               # CfnKnowledgeBase + DataSource

lambda/                         # Container Lambda source (new)
  Dockerfile                    # public.ecr.aws/lambda/python:3.12
  requirements.txt              # boto3>=1.35, pydantic v2, structlog
  app.py                        # routes /health, /query
  rag.py                        # Retrieve → prompt → InvokeModel → response
  schemas.py                    # pydantic request/response/error

streamlit_client/               # New, slim client (new)
  app.py                        # NO FAISS, NO Gemini — pure HTTP
  requirements.txt
  .streamlit/secrets.toml.example

sample-docs/                    # 6 small Markdown docs (new)
  refund-policy.md
  shipping-policy.md
  security-policy.md
  employee-handbook.md
  product-faq.md
  acceptable-use.md

scripts/                        # Operator one-shots (new)
  upload_docs.py                # sync sample-docs/ → S3 docs bucket
  start_ingestion.py            # StartIngestionJob + poll
  smoke_test.py                 # /health + auth + happy path
  rotate_api_key.py             # documented manual flow

tests/
  test_schemas.py               # pydantic round-trip
  eval/
    questions.json              # 10 questions, expected sources
    run_eval.py                 # POSTs all, writes eval_results.md
    eval_results.md             # committed sample run

legacy/                         # Old prototype, preserved as reference
  app.py, backend/, rag/, utils/, loaders/, frontend/, config.py, ...
  README.md                     # one-paragraph "this is the original"

README.md                       # rewritten per brief §14.3
.gitignore                      # secrets.toml, .env, cdk.out, vectorstore/
```

## CDK design

Two stacks, both in [infra/stacks/](infra/stacks/):

**`StorageStack`** — long-lived state
- `docs_bucket`: S3, versioned, SSE-S3, block-public, `autoDeleteObjects=True` for clean teardown
- `vector_bucket` + `vector_index`: via `constructs/s3_vectors.py` (AwsCustomResource calling `s3vectors:CreateVectorBucket` / `CreateIndex`; swap to L1 `CfnVectorBucket` when it lands — keep construct interface stable)
- KB service IAM role: `s3:Get*` on docs bucket, `s3vectors:*` on vector index, `bedrock:InvokeModel` on Titan v2 ARN only
- `CfnKnowledgeBase` (L1) with `storageConfiguration.type = "S3_VECTORS"`, `CfnDataSource` pointing at docs bucket
- `Secret` (`generateSecretString`, 32 chars alnum) — single source of truth for the API key
- Outputs: `KbId`, `KbArn`, `DocsBucketName`, `SecretArn`

**`ApiStack`** — request path
- `DockerImageFunction` (built from [lambda/Dockerfile](lambda/Dockerfile)), env vars `KB_ID`, `MODEL_ARN`, `LOG_LEVEL`
- Lambda execution role: `bedrock:Retrieve` on KB ARN, `bedrock:InvokeModel` on Haiku ARN, `logs:*`
- REST `RestApi` with `POST /query` and `GET /health`
- `ApiKey.value = secret.secretValueFromJson("apiKey")` — never typed by hand
- `UsagePlan`: throttle 2 rps / 5 burst, quota 200/day (hard cap)
- Log groups with `retention=ONE_WEEK`
- Outputs: `ApiUrl`, `ApiKeyId`

## Lambda handler design

Single handler in [lambda/app.py](lambda/app.py), routes on `event["resource"]`. Module-scope `bedrock-agent-runtime` and `bedrock-runtime` clients so warm invocations skip the ~2 s cold-start cost.

**Flow for `/query`** (in [lambda/rag.py](lambda/rag.py)):
1. Validate request via pydantic (`question` required, `top_k` clamped 1–10, default 5)
2. `bedrock-agent-runtime.retrieve(KB_ID, retrievalQuery={text}, retrievalConfiguration={vectorSearchConfiguration:{numberOfResults: top_k}})`
3. Build prompt: system "answer ONLY from context; if insufficient, reply `INSUFFICIENT_CONTEXT`; cite sources as [n]". User: numbered chunks + question. (Port the grounded prompt shape from [legacy/rag/generator.py:28-74](legacy/rag/generator.py#L28-L74) but strip "explain like 10" and related-questions branches.)
4. `bedrock-runtime.invoke_model(haiku, messages, max_tokens=800, temperature=0.1)`
5. If model returns `INSUFFICIENT_CONTEXT` → answer becomes "I don't have information about that in the knowledge base" with `confidence = min(computed, 0.2)`
6. Compute confidence from retrieve scores:
   `confidence = clamp(0.6*max(scores) + 0.3*mean(scores) + 0.1*(1 - stdev(scores)), 0, 1)`

**Response schema** (matches brief §6):
```json
{
  "answer": "...",
  "confidence": 0.84,
  "sources": [
    {"s3_uri": "s3://.../refund-policy.md", "document": "refund-policy.md",
     "score": 0.91, "snippet": "..."}
  ],
  "metadata": {
    "model": "anthropic.claude-3-haiku-20240307-v1:0",
    "retrieval_strategy": "bedrock-kb-s3vectors-titan-v2-topk",
    "request_id": "uuid4",
    "latency_ms": 1240
  }
}
```

**Error envelope**: `{"error": "InvalidRequest|Unauthorized|Internal", "message": "...", "request_id": "..."}` — 400 / 403 / 500. API GW handles 403 for missing/bad key.

## Sample documents

6 short Markdown files (~1–3 KB each), realistic enough to test retrieval and grounding:
1. `refund-policy.md` — 30-day window, exclusions, process
2. `shipping-policy.md` — domestic/intl, carriers, tracking
3. `security-policy.md` — passwords, MFA, data classification, incident response
4. `employee-handbook.md` — PTO, remote work, expense caps
5. `product-faq.md` — top 10 Q&A for fictional SaaS "Acme Notes"
6. `acceptable-use.md` — prohibited activities, enforcement, appeals

Loaded via [scripts/upload_docs.py](scripts/upload_docs.py) (boto3 sync) then [scripts/start_ingestion.py](scripts/start_ingestion.py) (`StartIngestionJob` + poll). **Not** via CDK `BucketDeployment` — keeps deploys fast and matches a real ingestion workflow.

## Auth wiring

- CDK creates the secret with `generateSecretString` → API Gateway `ApiKey` reads it via `SecretValue.secretsManager`. Operator never types a key.
- Reviewer fetches it once: `aws secretsmanager get-secret-value --secret-id <arn> --query SecretString --output text` and pastes into `streamlit_client/.streamlit/secrets.toml` (gitignored — `.example` file committed).
- Streamlit reads `st.secrets["API_KEY"]` and `st.secrets["API_BASE_URL"]`, falls back to env.
- Rotation: documented in [scripts/rotate_api_key.py](scripts/rotate_api_key.py) as a manual flow (update secret → `cdk deploy ApiStack` → redistribute).

## Implementation phases (sub-agent-sized units)

Each phase is a tight, self-contained brief suitable for dispatching to a sub-agent. Phases must run in order; each lists what the previous one delivers.

### Phase 1 — Scaffolding + sample docs + legacy move
- **Goal**: directory skeleton exists, 6 sample-docs written, legacy code relocated, `.gitignore` updated, README stub in place.
- **Files**: `sample-docs/*.md`, `streamlit_client/.streamlit/secrets.toml.example`, `.gitignore`, `README.md` (stub), `legacy/README.md`, and `git mv` of `app.py`, `backend/`, `rag/`, `utils/`, `loaders/`, `frontend/`, `config.py`, `requirements.txt`, `.streamlit/`, `data/`, `vectorstore/`, `setup.sh`, `docker-compose.yml`, `.devcontainer/`, all legacy `*.md` docs into `legacy/`.
- **DoD**: `git status` clean of unknowns; no AWS calls made.
- **Prereqs**: none.

### Phase 2 — CDK StorageStack
- **Goal**: `cdk synth` passes; manual `cdk deploy StorageStack` creates an ACTIVE KB in us-east-1.
- **Files**: [infra/app.py](infra/app.py), [infra/cdk.json](infra/cdk.json), [infra/requirements.txt](infra/requirements.txt), [infra/stacks/storage_stack.py](infra/stacks/storage_stack.py), [infra/constructs/s3_vectors.py](infra/constructs/s3_vectors.py), [infra/constructs/bedrock_kb.py](infra/constructs/bedrock_kb.py).
- **DoD**: synth clean locally; (deploy verification deferred to reviewer); construct API documented.
- **Prereqs**: Phase 1.

### Phase 3 — Lambda handler + container image
- **Goal**: handler builds as a container, validates schemas, and (against env-supplied `KB_ID` + `MODEL_ARN`) returns a schema-valid response.
- **Files**: [lambda/Dockerfile](lambda/Dockerfile), [lambda/requirements.txt](lambda/requirements.txt), [lambda/app.py](lambda/app.py), [lambda/rag.py](lambda/rag.py), [lambda/schemas.py](lambda/schemas.py), [tests/test_schemas.py](tests/test_schemas.py).
- **DoD**: `docker build` succeeds; `pytest tests/test_schemas.py` green; local invoke harness returns valid JSON shape (mockable when Bedrock unreachable).
- **Prereqs**: Phase 2 synth clean (need to know env var names).

### Phase 4 — CDK ApiStack
- **Goal**: `cdk deploy ApiStack` exposes `/health` and `/query` with API key enforced.
- **Files**: [infra/stacks/api_stack.py](infra/stacks/api_stack.py), updates to [infra/app.py](infra/app.py).
- **DoD**: synth clean; `curl` against deployed URL with valid key returns 200, bad key returns 403 (verification by reviewer).
- **Prereqs**: Phase 3 image builds; Phase 2 KB exists.

### Phase 5 — Operator scripts + ingestion
- **Goal**: documents uploaded and ingested; manual curl returns a real grounded answer.
- **Files**: [scripts/upload_docs.py](scripts/upload_docs.py), [scripts/start_ingestion.py](scripts/start_ingestion.py), [scripts/rotate_api_key.py](scripts/rotate_api_key.py).
- **DoD**: scripts runnable with only stack-output args; ingestion poller surfaces clear status.
- **Prereqs**: Phase 4.

### Phase 6 — Streamlit client + smoke test + eval harness
- **Goal**: local UI works against deployed API; smoke test green; eval table committed.
- **Files**: [streamlit_client/app.py](streamlit_client/app.py), [streamlit_client/requirements.txt](streamlit_client/requirements.txt), [scripts/smoke_test.py](scripts/smoke_test.py), [tests/eval/questions.json](tests/eval/questions.json), [tests/eval/run_eval.py](tests/eval/run_eval.py), [tests/eval/eval_results.md](tests/eval/eval_results.md).
- **DoD**: smoke test exits 0; eval markdown committed with ≥8 questions including 1 off-corpus and 1 ambiguous; Streamlit renders answer + sources + confidence badge.
- **Prereqs**: Phase 5 (ingested KB).

### Phase 7 — README + cleanup + hardening notes
- **Goal**: README satisfies brief §14.3; reviewer can stand up + tear down end-to-end in <30 min.
- **Files**: [README.md](README.md) (final). No code.
- **DoD**: README covers architecture diagram, services used, API contract, auth, RAG behavior, sample docs, changes from sample (with `legacy/` cross-refs), AI tools used, assumptions, data flow (only AWS — no external APIs), validation steps, `cdk destroy --all` instructions, hardening notes.
- **Prereqs**: all prior phases.

## Reused logic from legacy code

| Legacy file | What to port | Where |
|---|---|---|
| [legacy/rag/generator.py:28-74](legacy/rag/generator.py#L28-L74) | Grounded prompt shape (drop "explain like 10" + related questions) | [lambda/rag.py](lambda/rag.py) |
| [legacy/rag/generator.py:217-264](legacy/rag/generator.py#L217-L264) | Confidence formula shape (simplified, no keyword boost) | [lambda/rag.py](lambda/rag.py) |
| [legacy/backend/models/query.py](legacy/backend/models/query.py) | Response schema seed (rebuild as pydantic v2 in `lambda/schemas.py`) | [lambda/schemas.py](lambda/schemas.py) |

Everything else (FAISS, Gemini, document loaders, Streamlit upload UI, FastAPI routes) is **not** reused — the new architecture replaces it.

## Validation

End-to-end, after all phases:

1. `cd infra && cdk synth` → both stacks synth cleanly
2. `cdk deploy StorageStack ApiStack` → resources created in us-east-1
3. `python scripts/upload_docs.py` → 6 docs in S3
4. `python scripts/start_ingestion.py` → ingestion job COMPLETE (1–5 min poll)
5. `python scripts/smoke_test.py` → exits 0 (health 200, query 200, bad-key 403, no-key 403, empty-question 400)
6. `python tests/eval/run_eval.py` → writes `tests/eval/eval_results.md`
7. `streamlit run streamlit_client/app.py` → ask "What is the refund window?", see grounded answer + `refund-policy.md` in sources + confidence ~0.8
8. `cdk destroy --all` → all resources removed; vector index deletion handled by `AwsCustomResource.onDelete`; S3 buckets emptied by `autoDeleteObjects=True`

## Production hardening (README-only — do not build)

VPC + interface endpoints (Bedrock, Secrets, S3); Lambda in private subnets; AWS WAF on API GW (rate, geo, common rules); KMS CMKs on buckets, secret, log groups; structured JSON logs with `request_id`; X-Ray tracing; CloudWatch budget alarm at $10/$18; per-identity rate limiting via Cognito JWT (replaces API key); output PII redaction before logging; Bedrock model fallback (Haiku → Sonnet with backoff); async ingestion pipeline (S3 PutObject → EventBridge → Step Function → StartIngestionJob); SnapStart when available for container Lambdas.

## Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | S3 Vectors regional availability (us-east-1, us-west-2, eu-west-1, ap-southeast-2 at GA). | Pin region in [infra/app.py](infra/app.py); fail fast with clear error; README documents requirement. |
| 2 | Bedrock model access must be enabled manually in account. | README Step 0; smoke test maps `AccessDeniedException` to actionable hint. |
| 3 | Lambda cold start ~2 s with container image. | Module-scope clients; 1024 MB; no provisioned concurrency (cost). |
| 4 | KB ingestion is async (1–5 min). | `start_ingestion.py` polls every 10 s, 5 min timeout, prints status. |
| 5 | $20 budget runaway. | UsagePlan quota 200/day; CloudWatch alarm at $10; manual-only ingestion; Haiku + Titan v2 (cheapest); README documents `cdk destroy --all` as the only sure stop. |
