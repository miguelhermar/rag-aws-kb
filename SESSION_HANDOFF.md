# Session Handoff — RAG-AWS Productionization

**Purpose**: If you're a fresh Claude session opening this file, read it end-to-end. It contains everything you need to continue this project without losing context. The user expects you to resume from "Next Phase" without re-asking questions that are already settled below.

**Last updated**: 2026-05-24, after completing Phase 3 (Lambda handler, local-only — 23/23 pytest green, docker build green). Phase 1+2+3 are all uncommitted on the working tree.

---

## 1. Orientation — read these files in this order

1. **[PLAN.md](PLAN.md)** — the canonical implementation plan. All architectural decisions, phase breakdown, risks, hardening notes. Treat this as the source of truth for what we're building and why.
2. **[AWS Native Knowledge Base Agent Candidate Project Brief.md](AWS%20Native%20Knowledge%20Base%20Agent%20Candidate%20Project%20Brief.md)** — the original take-home brief. Re-read constraints (§"AWS Environment & Budget Guidelines", §6, §7) before making any architectural call.
3. **This file** — current state, live resource IDs, gotchas, next moves.
4. **[legacy/README.md](legacy/README.md)** — what the original prototype was and why it's preserved.

---

## 2. Project at a glance

The user (Miguel) is productionizing a Streamlit + Gemini + FAISS RAG prototype into an AWS-native architecture for an interview take-home. The deliverable is a CDK-deployed, authenticated AWS API queried from a local Streamlit client, with grounded answers + citations + confidence.

**Hard constraints**: $20 budget cap, no OpenSearch Serverless, no SageMaker, AWS CDK in Python, Claude 3 Haiku via Bedrock, sample docs in repo.

**User collaboration style**: senior engineer, fluent in cloud, expects:
- Opinionated recommendations, not menus.
- Sub-agent delegation when work is naturally chunk-able, but only well-briefed agents.
- Trust-but-verify pass on agent output before declaring done.
- Live deploy verification at each meaningful checkpoint — "don't just be okay on paper".
- Production-grade thinking (least-privilege IAM, observability, cost discipline).

---

## 3. Locked architectural decisions (do NOT re-litigate)

| Area | Choice | Settled via |
|---|---|---|
| Compute | AWS Lambda (container image, x86_64, 1024 MB, 30s) | AskUserQuestion early in session |
| Retrieval | Amazon Bedrock Knowledge Base **backed by S3 Vectors** (GA Jan 2026) | AskUserQuestion + WebSearch confirmation |
| LLM | Anthropic Claude 3 Haiku (`anthropic.claude-3-haiku-20240307-v1:0`) | Brief recommendation |
| Embeddings | Amazon Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`), 1024 dims | Required by S3 Vectors + KB |
| Auth | API Gateway REST API key + UsagePlan, key sourced from Secrets Manager | AskUserQuestion |
| IaC | AWS CDK in Python (`aws-cdk-lib==2.257.0`) | Brief mandate |
| Region | `us-east-1` (hard-pinned in CDK app) | AskUserQuestion (S3 Vectors GA region) |
| AgentCore | Out of scope (README only) | AskUserQuestion |
| Legacy code | Moved to `legacy/` subdirectory; preserved for diff | AskUserQuestion |
| Vector index params | dim=1024, distance=`cosine`, dtype=`float32` (lowercase enums — see §7) | Implementation detail |
| Chunking | FIXED_SIZE 300 tokens, 20% overlap | Plan agent decision; rationale in [infra/constructs/bedrock_kb.py:60-64](infra/constructs/bedrock_kb.py#L60-L64) |
| KB→Lambda call shape | `bedrock-agent-runtime.Retrieve` + manual `bedrock-runtime.InvokeModel` (NOT `RetrieveAndGenerate`) | Planned for Phase 3 — gives prompt control + clean source schema |

---

## 4. Phase status

| Phase | Goal | Status |
|---|---|---|
| 1 | Scaffolding + sample docs + legacy move | ✅ Complete |
| 2 | CDK StorageStack | ✅ Complete + **deployed and verified live** |
| 3 | Lambda handler + container image | ✅ Complete (local — pytest + docker build green; not yet deployed) |
| 4 | CDK ApiStack (Lambda + APIGW + auth) | ⏭️ NEXT |
| 5 | Operator scripts (upload_docs, start_ingestion, rotate_api_key) | Pending Phase 4 |
| 6 | Streamlit client + smoke test + eval harness | Pending Phase 5 |
| 7 | Final README + cleanup + hardening notes | Pending all prior |

See full phase briefs (goals, files, DoD, prereqs) in [PLAN.md §"Implementation phases"](PLAN.md).

---

## 5. Phase 1 — what we did

Done directly by Claude (no sub-agent — too mechanical):
- `git mv` every prototype file into `legacy/`: `app.py`, `backend/`, `rag/`, `utils/`, `loaders/`, `frontend/`, `config.py`, `requirements.txt`, `setup.sh`, `docker-compose.yml`, `.devcontainer/`, `.streamlit/`, `nginx/`, `__init__.py`, original `README.md` → `legacy/README-original.md`.
- Removed untracked caches: `__pycache__/`, `data/`, `vectorstore/`.
- Created new top-level dirs: `infra/`, `lambda/`, `streamlit_client/.streamlit/`, `scripts/`, `tests/eval/`, `sample-docs/`, `legacy/`.
- Wrote: new stub [README.md](README.md), [legacy/README.md](legacy/README.md) pointer, [streamlit_client/.streamlit/secrets.toml.example](streamlit_client/.streamlit/secrets.toml.example), `.gitignore` extensions (cdk.out/, cdk.context.json, secrets.toml, .aws-sam/, .pytest_cache/, etc.).

Done by sub-agent (`claude` type):
- 6 sample knowledge-base Markdown docs under `sample-docs/`. Fictional brand **Acme Notes** (collaborative note SaaS), domain `acmenotes.example`. Files: `refund-policy.md`, `shipping-policy.md`, `security-policy.md`, `employee-handbook.md`, `product-faq.md`, `acceptable-use.md`. Each 3-4 KB. Footer: `*Last updated: 2026-05-01*`.

**Important**: legacy `.env` (which contained a leaked Gemini API key) was NOT moved — it stays at root, gitignored. The production stack does not use Gemini at all.

---

## 6. Phase 2 — what we did and deployed

Done by sub-agent (`general-purpose` type) with mandatory WebSearch/WebFetch on current AWS APIs.

**Files created** (all under `infra/`):
- `app.py` (54 lines) — CDK app entry; instantiates StorageStack only for now; region hard-pinned `us-east-1`; uses `env -u PYTHONPATH python3 -P app.py` to avoid PYTHONPATH shadowing the `constructs` pypi package (the user has `PYTHONPATH=/opt/spark/python:` set globally).
- `cdk.json`, `requirements.txt`, `README.md`, `.gitignore`
- `stacks/storage_stack.py` (178 lines) — see file for the full picture
- `constructs/s3_vectors.py` (84 lines) — wraps `aws_s3vectors.CfnVectorBucket` + `CfnIndex` (native L1s; **no AwsCustomResource needed** — L1s shipped in aws-cdk-lib after S3 Vectors GA)
- `constructs/bedrock_kb.py` (100 lines) — wraps `CfnKnowledgeBase` + `CfnDataSource`
- `__init__.py` in `infra/`, `stacks/`, `constructs/`

**Pinned versions**: `aws-cdk-lib==2.257.0`, `constructs>=10.3.0,<11.0.0`, CDK CLI `2.1124.1`.

**Deployed live to AWS** — see §8 for resource IDs.

---

## 7. Critical lesson learned during Phase 2 deploy

**Live `cdk deploy` caught a bug that local `cdk synth` did not.**

`AWS::S3Vectors::Index` requires **lowercase** enum values:
- `dataType: "float32"` (NOT `"FLOAT32"`)
- `distanceMetric: "cosine"` (NOT `"COSINE"`)

The CDK L1 didn't validate this client-side; CloudFormation rejected the changeset with `🛑 FLOAT32 is not a valid enum value. Supported values: [float32]`.

**Fix applied in**: [infra/constructs/s3_vectors.py:38-39](infra/constructs/s3_vectors.py#L38-L39).

**Generalizable lesson**: for newly-GA AWS services, L1 enum validation can lag behind the service. Always do a real deploy at every checkpoint — synth is necessary but not sufficient. The user explicitly called this out: "not only on paper this is okay, but also in practice".

This fix is NOT yet committed. The repo working tree has the fix; if no commit has been made since 2026-05-23, run `git status` to confirm.

---

## 8. Live AWS state (as of 2026-05-23)

**Account**: `954863244564`, IAM user `admin_user`, region `us-east-1`.

**StorageStack — deployed, 14/14 resources `CREATE_COMPLETE` in 60s.**

Outputs written to [cdk-outputs.json](cdk-outputs.json) (gitignored? — check before committing):
```
KbId            = QWM9CGVIJL
KbArn           = arn:aws:bedrock:us-east-1:954863244564:knowledge-base/QWM9CGVIJL
DataSourceId    = 5ZGJAUZFDT
DocsBucketName  = storagestack-docsbucketecea003f-xgepmr8l4pya
DocsBucketArn   = arn:aws:s3:::storagestack-docsbucketecea003f-xgepmr8l4pya
VectorBucketArn = arn:aws:s3vectors:us-east-1:954863244564:bucket/rag-aws-vectors-244564
VectorIndexArn  = arn:aws:s3vectors:us-east-1:954863244564:bucket/rag-aws-vectors-244564/index/rag-aws-kb-index
ApiKeySecretArn = arn:aws:secretsmanager:us-east-1:954863244564:secret:ApiKeySecretF1B08E61-YjyCC7l72VIR-fpS2wI
```

**Live status verified via CLI**:
- KB `QWM9CGVIJL` → status `ACTIVE`, storage `S3_VECTORS`, embed Titan v2 ✅
- DataSource `5ZGJAUZFDT` → status `AVAILABLE`, chunking `FIXED_SIZE` ✅
- Docs bucket → exists, empty (sample-docs not yet uploaded — that's Phase 5) ✅
- Vector index `rag-aws-kb-index` → dim=1024, metric=cosine, dtype=float32 ✅
- API key secret → exists; value is `{"apiKey": "<32 alnum chars>"}` (never printed) ✅

**User pre-flight done before deploy**:
- Enabled Bedrock model access for Titan v2 AND Claude 3 Haiku in us-east-1 console
- Ran `cdk bootstrap aws://954863244564/us-east-1` (CDKToolkit stack exists)

**Cost incurred**: ≈$0 today. Idle cost going forward ≈$0.40/month (Secrets Manager flat fee). Everything else is pay-per-use.

---

## 9. Quick-resume commands

```bash
# From repo root
cd /Users/miguelhermar/Desktop/RAG-AWS

# Activate the CDK venv
source infra/.venv/bin/activate

# Sanity-check creds match the deployed account
aws sts get-caller-identity   # should show account 954863244564

# Verify StorageStack is still healthy
aws cloudformation describe-stacks --stack-name StorageStack --region us-east-1 \
  --query 'Stacks[0].StackStatus' --output text   # → CREATE_COMPLETE or UPDATE_COMPLETE

aws bedrock-agent get-knowledge-base --knowledge-base-id QWM9CGVIJL \
  --region us-east-1 --query 'knowledgeBase.status' --output text   # → ACTIVE

# Re-synth (no AWS calls)
cd infra && cdk synth StorageStack

# Tear down (when done with everything)
cdk destroy StorageStack --force
```

---

## 10. Open items / loose ends

- **Uncommitted changes**: Phase 1 + 2 are NOT committed to git yet. The user has not asked for a commit. Working tree has all the new files + the s3_vectors.py casing fix. Before committing, verify `cdk-outputs.json` is gitignored (it's not currently — consider adding to `.gitignore` since it contains account-specific ARNs; OR commit it as documentation, user's call).
- **The `.env` at root** still contains a leaked Gemini key (gitignored, so not pushed, but lives on the user's disk). Mention in final README that the new stack does not use Gemini and the user should rotate the key.
- **`legacy/.streamlit/secrets.toml`** was moved along with the rest of legacy — check if it contains secrets; it's covered by `.gitignore` patterns but worth a glance.
- **No tests written yet**. First tests come in Phase 3 (pytest on `lambda/schemas.py`).
- **`venv/`** at repo root is the legacy prototype's venv — gitignored, leave alone.

---

## 11. Phase 3 — what we did

Done by sub-agent (`general-purpose` type) on 2026-05-24, then trust-but-verified by Claude (re-read all files, re-ran pytest in `venv/`, 23/23 green; agent independently ran `docker build` successfully against `public.ecr.aws/lambda/python:3.12`).

**Files created**:
- [lambda/Dockerfile](lambda/Dockerfile) — base `public.ecr.aws/lambda/python:3.12`, copies requirements then `*.py`, `CMD ["app.handler"]`
- [lambda/requirements.txt](lambda/requirements.txt) — pinned `boto3==1.35.99`, `pydantic==2.9.2`, `structlog==24.4.0`
- [lambda/schemas.py](lambda/schemas.py) — pydantic v2 `QueryRequest`, `QueryResponse`, `Source`, `ResponseMetadata`, `ErrorEnvelope` matching brief §6 exactly
- [lambda/rag.py](lambda/rag.py) — module-scope `bedrock-agent-runtime` + `bedrock-runtime` clients; `retrieve`, `build_prompt`, `invoke_claude` (Anthropic Messages API with `system` field), `compute_confidence`, `run_query` with INSUFFICIENT_CONTEXT override
- [lambda/app.py](lambda/app.py) — handler routes on `httpMethod` + `path.endswith()`; reads `KB_ID`/`MODEL_ARN` at module scope (fail-fast); generates uuid4 `request_id`; measures `latency_ms` from handler entry; structlog JSON logs; catches `ValidationError` → 400 `InvalidRequest`, `ClientError` → 500 `Internal` (generic message, full traceback logged, never leaked)
- [tests/conftest.py](tests/conftest.py) — adds `lambda/` to `sys.path`; sets fake AWS creds + `KB_ID`/`MODEL_ARN` so `app.py` module-scope env reads succeed in tests
- [tests/test_schemas.py](tests/test_schemas.py) — 14 tests, request/response/error envelope round-trips + rejections
- [tests/test_rag.py](tests/test_rag.py) — 9 tests using `botocore.stub.Stubber` to mock both Bedrock clients; covers extract-fields, confidence math + clamping, end-to-end run_query, INSUFFICIENT_CONTEXT override, and `/health` makes zero AWS calls

**Sub-agent deviations from brief** (all accepted, see [agent run report]):
1. `run_query` signature gained `model_id_for_metadata` and `latency_fn` params — decouples the metadata model id (must be the plain id `anthropic.claude-3-haiku-20240307-v1:0`) from the inference target (`MODEL_ARN`, which may become a full inference-profile ARN in Phase 4), and lets `app.py` measure latency from handler entry rather than from inside `run_query`.
2. Routes match on `path.endswith("/health")` / `path.endswith("/query")` so APIGW stage prefixes don't break routing.
3. `compute_confidence` clamp-to-one test uses `pytest.approx(1.0)` due to 1e-16 float noise; math unchanged.

**Verification confirmed (2026-05-24)**:
- `python -m pytest tests/test_schemas.py tests/test_rag.py -v` → **23 passed in 0.24s** (Claude re-ran in `venv/`, independent of agent's run)
- `docker build -t rag-lambda:phase3 .` → green (agent's run; not re-verified by Claude since image rebuild is slow and not load-bearing)
- `grep` for hardcoded `QWM9CGVIJL`/account/secrets across `lambda/` + `tests/` → **0 matches**, all env-driven

---

## 12. Next phase brief (Phase 4 — ApiStack)

**Goal**: CDK stack that wraps the [lambda/](lambda/) container image into an API Gateway REST API with API-key auth and a usage plan, wired to the live KB from §8.

**Files to create**:
- `infra/stacks/api_stack.py` — defines `lambda_.DockerImageFunction` (asset = `../lambda`), 1024MB / 30s, x86_64; env vars `KB_ID` + `MODEL_ARN` sourced from StorageStack outputs (cross-stack ref via `Fn::ImportValue` or direct CDK reference); least-privilege IAM (only `bedrock:Retrieve` on KB ARN + `bedrock:InvokeModel` on Haiku model ARN + `logs:*`); `apigw.RestApi` with `/health` (no auth) and `/query` (`apiKeyRequired=True`); `UsagePlan` + `ApiKey` whose value is sourced from Secrets Manager (ApiKeySecretArn from §8); CloudWatch log group with 30-day retention.
- Update `infra/app.py` to instantiate `ApiStack` (taking StorageStack refs).
- (Optional) `tests/test_synth.py` — snapshot/synth smoke test.

**Then deploy live** — same pattern as Phase 2: `cdk synth ApiStack` then `cdk deploy ApiStack`, then `curl` the `/health` endpoint and a `/query` with the API key to confirm end-to-end.

**Cost note**: APIGW REST = ~$3.50/M requests (negligible). The Lambda will incur Bedrock InvokeModel cost only on `/query` hits (~$0.00025/1k input tokens for Haiku). Idle cost remains ~$0.40/month (Secrets Manager).

**How to execute**: same pattern — one `general-purpose` sub-agent with a tight brief, then trust-but-verify (read stack files, `cdk synth`, `cdk deploy`, live curl).

**Files to create**:
- `lambda/Dockerfile` — base `public.ecr.aws/lambda/python:3.12`
- `lambda/requirements.txt` — `boto3>=1.35`, `pydantic>=2`, `structlog`
- `lambda/app.py` — handler with internal routing on `event["resource"]`: `GET /health` (no AWS call), `POST /query` (full RAG)
- `lambda/rag.py` — Retrieve → prompt build → InvokeModel → confidence calc → response
- `lambda/schemas.py` — pydantic v2 request/response/error models
- `tests/test_schemas.py` — pytest round-trip tests

**Response schema** (must match brief §6 exactly):
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

**Error envelope**: `{"error": "InvalidRequest|Unauthorized|Internal", "message": "...", "request_id": "..."}` — 400 / 500 (403 handled by API GW).

**Confidence formula** (port simplified from [legacy/rag/generator.py:217-264](legacy/rag/generator.py#L217-L264)):
```
confidence = clamp(0.6*max(scores) + 0.3*mean(scores) + 0.1*(1 - stdev(scores)), 0, 1)
```
If Claude returns `INSUFFICIENT_CONTEXT`, override answer to "I don't have information about that in the knowledge base" and clamp confidence to ≤0.2.

**Prompt template**: port the grounded prompt shape from [legacy/rag/generator.py:28-74](legacy/rag/generator.py#L28-L74). Strip "explain like 10" and related-questions branches. System: "Answer ONLY from provided context. If insufficient, reply exactly `INSUFFICIENT_CONTEXT`. Cite sources as [n]." User: numbered chunks + the question.

**Boto3 client init**: module-scope (so warm Lambda invocations skip ~2s cold start). Two clients: `bedrock-agent-runtime` (for Retrieve) and `bedrock-runtime` (for InvokeModel).

**Verification before declaring Phase 3 done**:
1. `docker build` succeeds (`lambda/Dockerfile`)
2. `pytest tests/test_schemas.py` green
3. Local invoke with mocked event returns schema-valid JSON (mock Bedrock or run against real KB with creds — Lambda hasn't been deployed yet, so this is local-only)
4. Code reviewed for hard-coded secrets (none) and IAM expectations (handler assumes the role has `bedrock:Retrieve` + `bedrock:InvokeModel` — to be granted by ApiStack)

**How the user wants Phase 3 executed**: same pattern as Phase 2 — spawn one `general-purpose` sub-agent with a tight brief, then trust-but-verify by reading key files and running tests yourself.

---

## 12. Things to remember about this user

- They asked to "leverage the compact skill" but there is no literal `/compact` skill — interpret as "write a dense session handoff".
- They prefer terse, opinionated responses over menus.
- They have given standing authorization to deploy to their AWS account `954863244564/us-east-1`. Always confirm before destructive operations (`cdk destroy`, `aws s3 rb`).
- They appreciate cost transparency — quote idle/active costs when proposing AWS actions.
- They're using VS Code; file references should be markdown links `[name.ext](relative/path)`.

---

## 13. Closing pointer

If you start a new session: read this file, then [PLAN.md](PLAN.md), then ask the user: **"Ready to start Phase 4 (CDK ApiStack — wire the Lambda + APIGW + API-key auth, then deploy live)? Same pattern — sub-agent + trust-but-verify."** Do not re-derive decisions from scratch; the decisions in §3 are final unless the user explicitly reopens them.
