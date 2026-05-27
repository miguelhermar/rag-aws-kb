# Session Handoff — RAG-AWS Productionization

**Purpose**: If you're a fresh Claude session opening this file, read it end-to-end. It contains everything you need to continue this project without losing context. The user expects you to resume from "Next Phase" without re-asking questions that are already settled below.

**Last updated**: 2026-05-26 (evening), after **Phase 14 — CDK-native KB pre-seeding** (verified live): StorageStack now uses `aws_s3_deployment.BucketDeployment` + a Lambda-backed CustomResource to upload `sample-docs/*.md` and trigger `bedrock-agent.StartIngestionJob` on every `cdk deploy`. A SHA-256 of the seed corpus drives update semantics — unchanged redeploys are no-ops, changed seeds re-ingest the delta. `prune=False` preserves Phase 9 user uploads under `uploads/...`. Live verification: deploy + 7/7 smoke green; `cdk-seed` ingestion job indexed 6 docs in ~20s. The operator scripts (`upload_docs.py` + `start_ingestion.py`) remain for manual re-ingests but are no longer required on a fresh deploy. See §27.

**Previous milestone**: 2026-05-26 (mid-day), **Phase 13 — UI redesign + sources-on-reload persistence** (verified live): the Streamlit client got a ChatGPT-style redesign (Acme branding removed, conversations-only sidebar with time-bucket grouping + active highlight, settings popover, upload moved into an `st.dialog`, user-identity popover at the bottom, theme.toml + Inter font with auto light/dark). Two backend changes redeployed AgentStack + ApiStack to persist per-turn `sources` + `confidence` + `latency_ms` + `model_id` to AgentCore Memory as a base64-encoded `blob` sidecar so reloading a past conversation now shows the full assistant payload (not just text). See §26 for the full record, including the discovered AgentCore Memory blob round-trip quirk (Document type comes back as a Java `toString()` repr on `ListEvents` — workaround: base64-in-document-field + regex-extract on read; codified in [[feedback-agentcore-memory-blob-roundtrip]]). README ([README.md](README.md)) is the canonical reviewer entry point; §10.0 has the local TL;DR, §10.6 has the prod Streamlit Cloud runbook.

**Previous milestone**: 2026-05-25 (evening), **Phase 12 — production-like deploy** (verified live): the 4 CDK stacks were updated in place to add APIGW per-method throttling + multi-origin CORS + Cognito callback URLs for **Streamlit Community Cloud**. The Streamlit client is publicly reachable at **https://rag-aws-kb.streamlit.app**, hosted free on Streamlit Cloud (AWS App Runner stopped accepting new customers 2026-04-30; see [[feedback-aws-apprunner-unavailable]]). End-to-end browser walkthrough confirmed by Miguel. See §25. The local dev loop (`./scripts/run_streamlit.sh` from Phase 10) still works against the same prod stacks.

**Important**: the stacks are deployed in the AWS account / region `us-east-1` and the system is live at the URL above. If Miguel runs `cdk destroy --all`, every Cognito ID + API URL + App Client secret rotates — the *local* launcher auto-syncs on next run, but the Streamlit Cloud secrets must be re-pasted manually from `scripts/print_streamlit_cloud_secrets.py`. README §10.6 covers the post-redeploy ritual.

---

## 1. Orientation — read these files in this order

1. **[PLAN.md](PLAN.md)** — the canonical implementation plan. All architectural decisions, phase breakdown, risks, hardening notes. Treat this as the source of truth for what we're building and why.
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
| LLM | Anthropic **Claude Haiku 4.5** via US cross-region inference profile (`us.anthropic.claude-haiku-4-5-20251001-v1:0`); metadata model id is `anthropic.claude-haiku-4-5-20251001-v1:0` | Brief recommendation was Claude 3 Haiku, but it became LEGACY + Marketplace-gated post-2026 — re-locked to Haiku 4.5 on 2026-05-24 (see §11 Phase 5 incident notes) |
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
| 3 | Lambda handler + container image | ✅ Complete (local — pytest + docker build green) |
| 4 | CDK ApiStack (Lambda + APIGW + auth) | ✅ Complete + **deployed and smoke-tested live** |
| 5 | Operator scripts (upload_docs, start_ingestion, rotate_api_key) + end-to-end RAG | ✅ Complete + **6 docs ingested, in-corpus & off-corpus smoke tests green live** |
| 6 | Streamlit client + smoke test + eval harness | ✅ Complete + **smoke 4/4 green live, eval 100% in-corpus source-match, Streamlit boots clean** |
| 7 | Final README + cleanup + hardening notes | ✅ Complete — [README.md](README.md) covers all brief §14.3 sub-bullets + ASCII architecture diagram + evidence section + evaluation reflection + production hardening + cleanup |

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

## 8. Live AWS state (as of 2026-05-24 evening, end of Phase 6)

**StorageStack — deployed, `CREATE_COMPLETE`, 14 resources, ~62s wall-clock.**

**ApiStack — deployed, 22 resources `CREATE_COMPLETE`, ~70s wall-clock (docker layer cache warm).**

Outputs written to [cdk-outputs.json](cdk-outputs.json) (gitignored). DO NOT memorize specific IDs — they rotate on every redeploy. Read from `cdk-outputs.json` via `jq` and from Secrets Manager for the API key. 

**Live status verified via CLI + curl + scripts/smoke_test.py + tests/eval/run_eval.py (2026-05-24 evening, end of Phase 6)**:
- KB (from `cdk-outputs.json`) → status `ACTIVE`, storage `S3_VECTORS`, embed Titan v2 ✅
- DataSource (from `cdk-outputs.json`) → status `AVAILABLE`, chunking `FIXED_SIZE`; **6/6 sample-docs ingested** ✅
- Docs bucket → 6 `.md` files uploaded via `scripts/upload_docs.py` (idempotent) ✅
- Vector index `rag-aws-kb-index` → dim=1024, metric=cosine, dtype=float32 ✅
- API key secret → exists; value `{"apiKey": "<32 alnum chars>"}` (never printed) ✅
- **`scripts/smoke_test.py` → 4/4 PASS**: `/health` 200, `/query` valid+key 200 schema-valid + sources non-empty, `/query` no-key 403, `/query` empty-question 400 `InvalidRequest` ✅
- **`tests/eval/run_eval.py` → committed [tests/eval/eval_results.md](tests/eval/eval_results.md)**: source-match 100% (6/6 in-corpus), mean in-corpus confidence 0.813, off-corpus confidence clamped to 0.200, total wall-clock 20.2s for 8 questions ✅
- **`cdk diff` on both stacks**: 0 differences (zero IaC drift — Phase 6 touched only client/test/eval code) ✅

**User pre-flight done before deploy**:
- Bedrock model access for Titan v2 (`amazon.titan-embed-text-v2:0`) is `ACTIVE` in us-east-1.
- Haiku 4.5 inference profile `us.anthropic.claude-haiku-4-5-20251001-v1:0` is `ACTIVE` and does not require Marketplace subscription.
- `cdk bootstrap aws://<accountID>/us-east-1` (CDKToolkit stack exists, unchanged across redeploys).

**Cost incurred so far this session (Phase 6)**: ~$0.10 (full redeploy: docker push to ECR + ingestion + smoke runs + 8-question eval). Idle cost going forward ≈$0.40/month (Secrets Manager flat fee + ECR storage). Active cost: ~$0.0003 per `/query` (Haiku tokens dominate; APIGW + Lambda compute are rounding error). Cumulative project total still well under $1 of the $20 budget.

---

## 9. Quick-resume commands

```bash
# From repo root
cd /Users/miguelhermar/Desktop/RAG-AWS

# Activate the CDK venv
source infra/.venv/bin/activate

# Sanity-check creds match the deployed account
aws sts get-caller-identity   # should show account ID

# Verify StorageStack is still healthy
aws cloudformation describe-stacks --stack-name StorageStack --region us-east-1 \
  --query 'Stacks[0].StackStatus' --output text   # → CREATE_COMPLETE or UPDATE_COMPLETE

KB_ID=$(jq -r '.StorageStack.KbId' cdk-outputs.json)
aws bedrock-agent get-knowledge-base --knowledge-base-id "$KB_ID" \
  --region us-east-1 --query 'knowledgeBase.status' --output text   # → ACTIVE

# Re-synth (no AWS calls)
cd infra && cdk synth

# Live end-to-end smoke (in-corpus question)
API_URL=$(jq -r '.ApiStack.ApiUrl' cdk-outputs.json)
SECRET_ARN=$(jq -r '.StorageStack.ApiKeySecretArn' cdk-outputs.json)
API_KEY=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" \
  --region us-east-1 --query SecretString --output text \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['apiKey'])")
curl -s -X POST "${API_URL}query" \
  -H "x-api-key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"question":"What is the refund window for monthly plans?","top_k":3}' | jq .

# Tear down (when done with everything — destructive, confirm before running)
cd infra && cdk destroy ApiStack StorageStack --force
```

---

## 10. Open items / loose ends

- **Commits so far**: `e3925e5` (Phases 1-3), `e7c6d14` (Phase 4 ApiStack), `20add17` (Phase 5 scripts + ingestion + model relock + INSUFFICIENT_CONTEXT fix). Working tree clean at end of session.
- **The `.env` at root** still contains a leaked Gemini key from the legacy prototype (gitignored, so not pushed, but lives on the user's disk). Mention in the final README (Phase 7) that the new stack does not use Gemini and the key should be rotated/deleted by the user.
- **`legacy/.streamlit/secrets.toml`** was moved with the rest of `legacy/` — checked, contains only commented-out placeholders, safe.
- **Tests written**: 24 unit tests in `tests/test_schemas.py` + `tests/test_rag.py` (botocore Stubber, no AWS); 5 synth-time tests in `tests/test_synth.py` (require `aws_cdk` — run via `infra/.venv`). Eval harness (`tests/eval/`) lands in Phase 6.
- **`venv/`** at repo root is the legacy prototype's venv — gitignored. Convenient because it has pytest + boto3 already installed; used for running the non-synth tests. Leave alone.
- **`infra/.venv/`** is the CDK venv (has `aws-cdk-lib`, `constructs`). Used for `cdk synth`, `cdk diff`, `cdk deploy`, and `tests/test_synth.py`.
- **Two python environments** — annoying but works: `venv/` for unit tests + script execution, `infra/.venv/` for CDK. Phase 7 cleanup could consolidate, but it's low-priority.

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
- `grep` for hardcoded KB IDs / account IDs / secrets across `lambda/` + `tests/` → **0 matches**, all env-driven

---

## 12. Phase 4 — what we did

Done by sub-agent (`general-purpose` type) on 2026-05-24, then trust-but-verified by Claude (read api_stack.py + app.py + test_synth.py, independently called `aws cloudformation describe-stacks` and `curl /health` → 200).

**Files created/modified**:
- [infra/stacks/api_stack.py](infra/stacks/api_stack.py) — 144 lines. `DockerImageFunction` (asset = `../lambda`), 1024MB/30s, x86_64; env `KB_ID`+`MODEL_ARN`+`LOG_LEVEL=INFO`; explicit `LogGroup` with 30-day retention (avoids CDK's `log_retention` custom-resource Lambda); least-privilege IAM (`bedrock:Retrieve` on KB ARN only, `bedrock:InvokeModel` on Haiku ARN only); `RestApi` REGIONAL, prod stage, `MethodLoggingLevel.INFO`, metrics on, tracing off; `/health` (no key) + `/query` (key required); `ApiKey` whose value resolves via `secret.secret_value_from_json("apiKey").unsafe_unwrap()` (server-side dynamic ref — value never lands in synth template); `UsagePlan` rate 5/burst 10/quota 1000/day.
- [infra/app.py](infra/app.py) — instantiates `ApiStack` with cross-stack ref to `StorageStack` (in-memory, NOT `Fn.import_value` — both stacks live in the same `cdk.App`); `api_stack.add_dependency(storage_stack)` to lock deploy order.
- [tests/test_synth.py](tests/test_synth.py) — 5 pytest synth-time assertions (single Lambda, single RestApi, env contains `KB_ID`+`MODEL_ARN`, `/query` requires key, `/health` does not).

**Sub-agent deviations from brief** (all accepted):
1. **Explicit `LogGroup` instead of `log_retention=` param**: avoids CDK provisioning a `Custom::LogRetention` framework Lambda, which would have meant 2× `AWS::Lambda::Function` in the stack and broken the "exactly 1 Lambda" assertion. Functionally identical, strictly cleaner.
2. **Smoke-test API-key extraction**: agent used `jq` against `cdk-outputs.json` rather than the hard-coded ARN in the brief, since the StorageStack ARN had rotated. API key was held in a shell variable only; never printed.

**Live verification (2026-05-24)** — three curl smoke tests against the deployed `ApiUrl` (from `cdk-outputs.json`):
- `GET /health` → `200 {"status":"ok"}` ✅
- `POST /query` without `x-api-key` → `403 {"message":"Forbidden"}` ✅ (auth wired)
- `POST /query` with valid key + sample question → `200`, schema-valid JSON, `confidence=0.0`, `sources=[]`, `answer="I don't have information about that in the knowledge base."` ✅ — this is the **expected** behaviour: docs haven't been ingested yet, KB returns 0 chunks, INSUFFICIENT_CONTEXT override fires. Confirms the wiring end-to-end (APIGW → Lambda → Bedrock Retrieve → Bedrock InvokeModel → response). Phase 5 will upload docs + start an ingestion job and the same curl should then return a real grounded answer.

---

## 13. Phase 5 — what we did

Done by sub-agent (`general-purpose` type) on 2026-05-24, then trust-but-verified by Claude (read all 3 scripts + README, independently ran end-to-end curl against the deployed API).

**Files created** (all under `scripts/`):
- [scripts/upload_docs.py](scripts/upload_docs.py) — 105 lines. Walks `sample-docs/*.md`, computes local MD5, compares against S3 ETag, uploads only on mismatch. Reads `DocsBucketName` from `cdk-outputs.json`.
- [scripts/start_ingestion.py](scripts/start_ingestion.py) — 117 lines. Calls `bedrock-agent.start_ingestion_job` then polls `get_ingestion_job` every 10s with 5-minute timeout (PLAN.md Risk #4). Terminal status set: `{COMPLETE}` OK, `{FAILED, STOPPED}` failure. Prints scanned/indexed/failed counts on success.
- [scripts/rotate_api_key.py](scripts/rotate_api_key.py) — 107 lines. Generates a 32-char alnum value (`secrets.choice` over `ascii_letters + digits`), calls `put_secret_value`, then prints the manual recipe to propagate to APIGW (see "Rotation flow" below). NEVER prints the new value.
- [scripts/README.md](scripts/README.md) — 155 lines. One section per script + a "Typical workflow" + the rotation recipe.

**Rotation flow — verified against AWS docs, NOT obvious**:
- The CDK `ApiKey` was constructed with `secret.secret_value_from_json("apiKey").unsafe_unwrap()`, which emits a CFN dynamic reference `{{resolve:secretsmanager:<arn>:SecretString:apiKey}}`. Per the [CFN dynamic-references docs](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/dynamic-references-secretsmanager.html), this resolves exactly once at create/update time. Rotating the secret value does NOT push the new value into APIGW.
- Per the [APIGW patch-operations docs](https://docs.aws.amazon.com/apigateway/latest/api/patch-operations.html), `UpdateApiKey` does NOT support patching `/value` — only `/customerId`, `/description`, `/enabled`, `/labels`, `/name`, `/stages`. So there's no SDK call to in-place-update an API key's value.
- **Correct flow** (the one the script implements): rotate the secret → operator bumps any property on the `apigw.ApiKey` construct (e.g., `description="rotated-YYYY-MM-DD"`) → `cdk deploy ApiStack` re-resolves the dynamic reference. A pure no-op deploy will not suffice.

**Live verification (2026-05-24)**:
- `python scripts/upload_docs.py` → 6 .md files uploaded; second run all 6 skipped (idempotency proven).
- `python scripts/start_ingestion.py` → ingestion `COMPLETE` in ~10s. `numberOfDocumentsScanned=6`, `numberOfNewDocumentsIndexed=6`, `numberOfDocumentsFailed=0`.
- End-to-end `/query` smoke (independent curl by Claude):
  - **In-corpus**: "What is the refund window for monthly plans?" → real grounded answer with `[1]` citations, 3 sources (top 2 from `refund-policy.md`), confidence ~0.67. ✅
  - **Off-corpus**: "How do I report a security incident at Acme Notes?" → canned "I don't have information about that in the knowledge base.", confidence clamped to 0.2, sources still returned for transparency. ✅ (See §14 below for the bug this exposed and how it was fixed.)

**Cost incurred this phase**: ingestion ~$0.0005 (Titan v2 embeddings on ~25k tokens); ~6 Claude Haiku 4.5 smoke calls ~$0.02; APIGW + Lambda compute rounding error. Project cumulative still well under $1 of the $20 budget.

---

## 14. Phase 5 incidents and corrective actions

Two issues surfaced during Phase 5 that required corrective action. Both are now resolved; recording here so future sessions understand the lineage.

**Incident A — Model deprecation + IaC drift.**
- The Phase 5 sub-agent hit `AccessDeniedException` when invoking `anthropic.claude-3-haiku-20240307-v1:0` — Bedrock has marked the model LEGACY post-2026 and gates it behind an AWS Marketplace subscription.
- The sub-agent asked Miguel directly (in its own tool-use loop, NOT visible in the main agent's context) for guidance, and Miguel authorized the swap to Claude Haiku 4.5 rather than enable the Marketplace subscription. So the model choice itself was authorized — **what was wrong was the mechanism**: the agent applied the change by editing the **live Lambda** (`MODEL_ARN`, `MODEL_ID`, IAM policy) directly via AWS CLI, instead of updating [infra/stacks/api_stack.py](infra/stacks/api_stack.py) and redeploying via CDK. This created IaC drift — the next `cdk deploy ApiStack` would have reverted the change.
- The main agent (Claude orchestrator) initially read the situation as "fabricated authorization" because it could see the out-of-band change without the side-conversation context. Miguel clarified after the fact. **Lesson for future sessions**: if the main agent suspects a sub-agent is acting without authorization, ASK Miguel to confirm before assuming — sub-agent ↔ user side conversations exist and are real, just not visible in main context. See [[feedback-no-out-of-band-aws-changes]] for the codified rule.
- Corrective action (committed in `20add17`):
  - Re-locked model to **Claude Haiku 4.5** via cross-region inference profile `us.anthropic.claude-haiku-4-5-20251001-v1:0` (see §3).
  - Updated [api_stack.py](infra/stacks/api_stack.py) to emit `MODEL_ARN=<inference profile id>`, `MODEL_ID=anthropic.claude-haiku-4-5-20251001-v1:0`, and IAM `bedrock:InvokeModel` on the inference profile ARN + foundation-model ARNs in us-east-1/us-east-2/us-west-2 (the regions the US profile fans out to).
  - Ran `cdk deploy ApiStack` — `UPDATE_COMPLETE` in 33.6s. Subsequent `cdk diff` showed no differences (drift formally closed; source = CFN template = live).
  - Saved feedback memory [[feedback-no-out-of-band-aws-changes]] so future sub-agents are explicitly briefed: changes to live AWS go through CDK source only, even when the user authorizes the *change*; the agent should ask for the IaC-vs-out-of-band distinction explicitly if unclear.

**Incident B — INSUFFICIENT_CONTEXT parsing bug exposed by the model swap.**
- After ingesting docs, an off-corpus question ("How do I report a security incident?") returned the LLM's *elaborated* refusal (`"INSUFFICIENT_CONTEXT\n\nThe provided context does not contain..."`) instead of the canned `"I don't have information about that in the knowledge base."`. Confidence was 0.81 (not clamped to ≤0.2 as the brief requires).
- Root cause: [lambda/rag.py](lambda/rag.py) checked `answer.strip() == INSUFFICIENT` (exact equality). Claude 3 Haiku followed "reply exactly" literally; Claude Haiku 4.5 elaborates after the token.
- Fix:
  - Relaxed the check to `answer.strip().startswith(INSUFFICIENT)`.
  - Strengthened the system prompt to be unambiguous about the literal-token requirement.
  - Added `tests/test_rag.py::test_run_query_insufficient_context_with_elaboration` covering the new behavior.
  - Redeployed Lambda; off-corpus smoke now returns the canned answer + confidence 0.2. ✅
- All unit tests green (24 passed).

---

## 15. Phase 6 — what we did

Done by sub-agent (`general-purpose` type) on 2026-05-24 evening after Miguel and the orchestrator did the full StorageStack + ApiStack redeploy + doc upload + ingestion together. Trust-but-verified by Claude orchestrator (read all 5 new files, independently re-ran `scripts/smoke_test.py` against live API, inspected committed [tests/eval/eval_results.md](tests/eval/eval_results.md), confirmed `cdk diff` shows 0 differences on both stacks → no IaC drift).

**Files created**:
- [streamlit_client/app.py](streamlit_client/app.py) — 112 lines. `st.chat_input` + `st.chat_message` chat UI. Sidebar with `top_k` slider (default 5) + Clear-conversation button. Reads `API_BASE_URL` + `API_KEY` from `st.secrets` (fails loudly with `st.stop()` if missing). Confidence badge via `st.success`/`st.warning`/`st.error` thresholds (≥0.7 / ≥0.4 / <0.4). Expandable Sources section with `document`, `s3_uri`, score-to-3-decimals, snippet. Handles non-200 + network errors via `st.error`. Replays session history.
- [streamlit_client/requirements.txt](streamlit_client/requirements.txt) — `streamlit==1.57.0`, `requests==2.34.2` (pinned to latest stable verified on PyPI day-of).
- [scripts/smoke_test.py](scripts/smoke_test.py) — 189 lines. 4 checks: `/health` 200+body match, `/query` valid+key 200 + pydantic-validates `QueryResponse` from `lambda/schemas.py` + non-empty sources + confidence in [0,1], `/query` no-key 403, `/query` empty-question 400 with `error == "InvalidRequest"`. Reads defaults from `cdk-outputs.json` via argparse (same pattern as `scripts/upload_docs.py`). Never prints the API key.
- [tests/eval/questions.json](tests/eval/questions.json) — 8 items, one per category required by the brief: 6 in-corpus (one per sample doc topic), 1 ambiguous ("What happens to my data if I cancel my account?"), 1 off-corpus ("What is the office WiFi password?").
- [tests/eval/run_eval.py](tests/eval/run_eval.py) — 172 lines. POSTs each question with `top_k=5`, paces 250ms between calls (UsagePlan rate is 5/s), writes [tests/eval/eval_results.md](tests/eval/eval_results.md) with full per-question table + aggregate metrics block (source-match rate excluding ambiguous + off-corpus, mean confidence per category, total wall-clock and API latency).
- [tests/eval/eval_results.md](tests/eval/eval_results.md) — committed sample run from one execution against the live API.

**Live verification (2026-05-24 evening)**:
- `scripts/smoke_test.py` → 4/4 PASS (re-run independently by orchestrator after agent's run).
- `tests/eval/run_eval.py` → source-match 100% (6/6 in-corpus), mean in-corpus confidence **0.813**, off-corpus question clamped to **0.200** (canned INSUFFICIENT_ANSWER), 20.2s wall-clock for 8 questions.
- `cdk diff` on both stacks → **0 differences** (agent did NOT touch infra; rule from [[feedback-no-out-of-band-aws-changes]] held).
- Streamlit headless boot → HTTP 200 from `localhost:8501`, clean uvicorn startup (no traceback). Visual UI rendering was eyeballed by Miguel.

**Sub-agent deviations from brief** (all accepted):
1. **Q07 (ambiguous question) returned INSUFFICIENT_ANSWER + 0.200 confidence** rather than a multi-source synthesis. The model retrieved chunks (top hit `acceptable-use.md`, score 0.577) but judged the context insufficient. This is defensible grounded-RAG behavior on a genuinely ambiguous question; the eval file documents it transparently. Phase 7 hardening-notes mention this is a candidate for prompt-tuning if a future product owner wants the system to attempt multi-doc synthesis on ambiguous questions.
2. **Streamlit was launched via `./venv/bin/python -m streamlit`** rather than the `streamlit` script entrypoint, because the repo-root `venv/` has stale `#!` shebangs from when the directory was named `RAG-demo/`. No changes were made to the venv. Phase 7 cleanup candidate (or just delete the legacy venv and rebuild — `infra/.venv/` is the only one CDK touches).

**Empty-question validator note**: [lambda/schemas.py:8](lambda/schemas.py#L8) already had `Field(min_length=1)` on `question` (added in Phase 5), and [tests/test_schemas.py:33-35](tests/test_schemas.py#L33-L35) already covered the rejection case. The brief asked for these to be added in Phase 6; turned out they were already present, so no schema/Lambda change was needed and no redeploy was triggered for this reason. (StorageStack + ApiStack were redeployed anyway because Miguel destroyed them between sessions.)

**Cost incurred this phase**: ~$0.10 total (full redeploy = docker push to ECR + ingestion run + ~12 Haiku 4.5 calls across smoke + eval). Cumulative project total still well under $1 of the $20 budget.

---

## 16. Phase 7 — what we did

Done **inline by the Claude orchestrator** (no sub-agent — docs-only synthesis benefits from full session context). 2026-05-24 late evening.

**Files written**:
- [README.md](README.md) — 545-line reviewer-facing README, full rewrite of the Phase-1 stub. Covers every brief §14.3 sub-bullet plus brief §11 (evaluation with weak/ambiguous case discussion).

**README structure** (15 sections):
1. Title + live-status one-liner.
2. ASCII architecture diagram (~75 lines, numbered request-flow steps 1-8 + secret-flow callout) + 1.1 summary + 1.2 key-tradeoffs table covering 9 decisions.
3. AWS services + CDK stacks (single combined table; both StorageStack + ApiStack explained).
4. API contract & authentication (endpoints, request, response, error envelope, full auth flow including rotation gotcha + production path to Cognito).
5. RAG behavior (ingestion, retrieval, prompt construction, confidence formula, INSUFFICIENT_ANSWER override).
6. Sample documents table.
7. Demo evidence — full live request/response with CloudWatch log lines.
8. Evaluation: aggregate metrics + good examples + weak/ambiguous (q07 + q08 framed as "design success not failure") + reflection on improvements.
9. Changes from sample project (12-row table mapping legacy → this solution + why).
10. AI tools used during development (Claude Code orchestration story + 2 documented incidents).
11. Production hardening — what's implemented vs what's documented-but-not-implemented.
12. Run instructions (deploy / seed / validate / streamlit / teardown with cost).
13. Repository layout.
14. Assumptions, known limitations, data flow (AWS-only callout), testing matrix.
15. Closing.

**Verification done** before commit:
- `wc -l README.md` → 545 lines (~4-5 pages dense markdown — slightly over brief's 2-4 page guideline because Miguel explicitly asked for "very, very complete").
- 28 file references checked, all resolve.
- `curl ${API_URL}health` → 200 (live system still responding, so the "Live as of last commit" claim in §15 holds).

**Sub-agent deviations**: none (no sub-agent used).

**Open items left for the user**:
- Visual review of the README rendered on GitHub (markdown table widths + ASCII diagram width survive in GH's monospace code block) — orchestrator can't browse.
- Optional: delete `venv/` and recreate (the legacy `RAG-demo/` shebangs are documented as a known limitation in [README.md §13.2](README.md) but harmless; cleanup is a nice-to-have).
- Optional: rotate / delete the leaked Gemini key in legacy `.env` (gitignored, never pushed, but lives on disk) — flagged in README §8 changes table.
- Optional: `cdk destroy` after submission to zero idle cost. README §11.5 documents the command + the lingering-ECR-repo cleanup.

**Cost incurred this phase**: $0 active (docs-only; no AWS calls beyond one `/health` curl). Cumulative project total still well under $1.

---

## 17. Project status — core DONE, optional extensions pending user election

All 7 *required* phases complete. The deliverable is interview-ready and the system is live in `us-east-1` at the URL in §8 (the README is the canonical reviewer doc; this handoff is now mostly an archival record of how we got here).

**However**: Miguel has identified a list of *optional extensions* (brief §"Optional extensions") that he is considering implementing in **future fresh sessions**, one at a time, based on what he decides to prioritize. The full candidate list is preserved in [§19](#19-optional-extensions--candidate-list-for-future-sessions) below. Do not implement any of them proactively — wait for Miguel to elect specific ones session-by-session.

The README is the single source of truth for any future reader (reviewer or future-Claude). This handoff doc retains value for:
- The phase-by-phase build narrative (§5, §6, §11, §12, §13, §15, §16).
- The two production lessons in §14 (out-of-band-AWS-changes rule + model-deprecation incident).
- Live resource IDs as last observed (§8) — these are stale the moment Miguel runs `cdk destroy`.
- **The optional-extension shortlist in §19** — the menu of next moves Miguel is considering.

---

## 18. Things to remember about this user

- They asked to "leverage the compact skill" but there is no literal `/compact` skill — interpret as "write a dense session handoff".
- They prefer terse, opinionated responses over menus.
- They have given standing authorization to deploy to their AWS account. Always confirm before destructive operations (`cdk destroy`, `aws s3 rb`).
- They appreciate cost transparency — quote idle/active costs when proposing AWS actions.
- They're using VS Code; file references should be markdown links `[name.ext](relative/path)`.
- They sometimes `cdk destroy` between sessions to zero out idle cost — at session start, always run `aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE --region us-east-1` (or just `jq` on `cdk-outputs.json` + a `describe-stacks` call) to confirm whether StorageStack/ApiStack are still up before assuming any live IDs are valid.

---

## 19. Optional extensions — candidate list for future sessions

Miguel has flagged the following as *optional extensions* to consider for future fresh sessions. **Each is an independent piece of work; Miguel will elect one (or a small batch) per session.** Do not implement any of these unless Miguel explicitly asks for it in the current session. If Miguel asks "what should we work on next?", surface this list and recommend one with the highest ROI for an interview-presentable project, but let him pick.

The list, verbatim from the brief, is:

- CI/CD pipeline for CDK deployment.
- ~~Streaming responses from the API to Streamlit.~~ **✅ DONE in Phase 9a (2026-05-25)** — see §22.
- ~~Upload or ingestion endpoint for new documents.~~ **✅ DONE in Phase 9b (2026-05-25)** — see §22.
- ~~DynamoDB-backed chat/session history.~~ **✅ DONE in Phase 8 (2026-05-25)** — see §21.
- ~~Amazon Bedrock AgentCore Runtime + Amazon Bedrock AgentCore Memory~~ **✅ DONE in Phase 8 (2026-05-25)** — see §21.
- Guardrails or safety filters.
- Human feedback collection.
- Cost controls and token usage tracking.

**Bonuses delivered (not on the original menu)**: Cognito User Pool + JWT auth replaced the API-key path entirely (Phase 8).

**Reviewer-facing accuracy**: [README.md](README.md) has been fully rewritten for the Phase 8 + 9 state (architecture diagram with 4 stacks, Cognito flow, streaming SSE protocol, upload + ingestion contracts, AgentCore Memory + DDB shape, live verification evidence). No further README work is needed before the next extension.

**Notes for future-Claude when one of these is elected**:
- Most touch infra. The "no out-of-band AWS changes" rule ([[feedback-no-out-of-band-aws-changes]]) still applies — every AWS-affecting change goes through CDK source + `cdk deploy`, with `cdk diff` clean as the success gate.
- Each extension should probably be its own commit (and own SESSION_HANDOFF.md "Phase 8+" record). Treat them as discrete phases — same brief + sub-agent + trust-but-verify pattern that worked for Phases 2-6.
- Some are mutually informing (e.g., Cognito JWT + DynamoDB session history naturally pair; CloudWatch dashboard + X-Ray tracing pair; CI/CD + dev/prod envs pair). When Miguel elects one, *ask* if any pairing makes sense before you start.
- A few interact with [README.md](README.md) production-hardening section (§10) — implementing them moves an item from "documented-but-not-built" to "implemented". Keep that section accurate as items land.
- The system may or may not be deployed when a future session starts. Check stacks first (see §20 step 4) before assuming any live IDs are valid.

---

## 21. Phase 8 — what we did

Elected from §19's optional-extension menu on 2026-05-25. Brief: "DynamoDB-backed chat/session history + Amazon Bedrock AgentCore Runtime + AgentCore Memory", modeled on iteration 3 of [`aws-samples/sample-ai-agent-architectures-agentcore`](https://github.com/aws-samples/sample-ai-agent-architectures-agentcore) but with several deliberate improvements over the sample.

### 21.1 Locked architectural decisions (do NOT relitigate)

| Area | Choice | Rationale |
|---|---|---|
| Auth | **Cognito User Pool + JWT only** (API key removed) | Matches the sample; required for per-user `actor_id` (= JWT `sub`). Test user `demo` pre-created by CDK; permanent password in Secrets Manager. |
| Agent depth | **Hybrid** — `agent/rag.py` packaged as `BedrockAgentCoreApp` deployed to AgentCore Runtime; `/query` Lambda is a thin proxy via `bedrock-agentcore:invoke_agent_runtime`. **No LangGraph.** | Delivers both the "Runtime" and "Memory" bullets of the brief without LangChain framework weight. Keeps the existing RAG behavior (Retrieve + InvokeModel + INSUFFICIENT_CONTEXT override) byte-identical. |
| Memory shape | **Native `conversational` payload**, NOT msgpack/LangGraph | Console-introspectable; one `create_event` per turn with two payload items (USER + ASSISTANT). The sample uses LangGraph's `AgentCoreMemorySaver` (msgpack blobs); we improved on that. |
| AgentCore IaC | **CDK stable L1s** (`aws_cdk.aws_bedrockagentcore.CfnRuntime` + `CfnMemory`) | The sample uses the `agentcore` CLI out-of-band + SSM glue. CFN coverage shipped Sept 2025; CDK 2.257.0 already ships the L1s. We're 100% CDK, fully `cdk diff`-able, no out-of-band drift. |
| Container | **`DockerImageAsset` (arm64)** for the agent | Same pattern as the existing Lambda image. AgentCore Runtime *only* accepts arm64 — caught live on first deploy (error: `Supported platforms: [arm64]`). |
| DDB shape | PK `actor_id` (S), SK `session_id` (S), attrs `conversation_name`, `created_at`, PAY_PER_REQUEST, no GSI/TTL/streams | Mirrors sample. Used only for sidebar listing; messages live in AgentCore Memory. |
| Memory expiry | `EventExpiryDuration=30` days | Short-term raw events only; no semantic / summary strategies. |
| Title | LLM-generated 3-5 word title (≤50 chars), written on first turn | Detected via `list_events(maxResults=1)` returning empty. |
| `runtimeSessionId` | Aligned with app `session_id` (padded to ≥33 chars if shorter) | Better audit trail than the sample, which lets AgentCore auto-generate it. |

### 21.2 Stack topology (4 stacks)

```
StorageStack ──────────► AuthStack
     │                       │
     │                       ▼
     ▼                  AgentStack
  DDB + Memory          (AgentCore Runtime
  + KB + S3 Vectors      + arm64 container
  + DocsBucket)          + IAM role)
                              │
                              ▼
                          ApiStack
                          (REST API + Cognito
                           authorizer + Lambda
                           proxy + /conversations*)
```

### 21.3 Files (this phase)

**Created**: [infra/stacks/auth_stack.py](infra/stacks/auth_stack.py), [infra/stacks/agent_stack.py](infra/stacks/agent_stack.py), [agent/app.py](agent/app.py), [agent/rag.py](agent/rag.py), [agent/schemas.py](agent/schemas.py), [agent/Dockerfile](agent/Dockerfile), [agent/requirements.txt](agent/requirements.txt), [lambda/conversations.py](lambda/conversations.py), [scripts/get_id_token.py](scripts/get_id_token.py), [tests/test_agent.py](tests/test_agent.py).

**Heavy edits**: [infra/stacks/api_stack.py](infra/stacks/api_stack.py) (full rewrite — Cognito authorizer + 4 routes), [infra/stacks/storage_stack.py](infra/stacks/storage_stack.py) (+ DDB + AgentCore Memory), [infra/app.py](infra/app.py) (4 stacks), [lambda/app.py](lambda/app.py) (multi-route proxy), [lambda/schemas.py](lambda/schemas.py) (+ session_id, + conversation_name), [streamlit_client/app.py](streamlit_client/app.py) (`st.login()` + sidebar + replay), [scripts/smoke_test.py](scripts/smoke_test.py), [tests/eval/run_eval.py](tests/eval/run_eval.py), [tests/test_synth.py](tests/test_synth.py), [tests/test_schemas.py](tests/test_schemas.py), [tests/conftest.py](tests/conftest.py).

**Deleted**: `lambda/rag.py` (logic moved to `agent/`), `tests/test_rag.py`, `scripts/rotate_api_key.py` (no API key anymore).

### 21.4 Live verification (2026-05-25 mid-day)

All 4 stacks deployed to `us-east-1` in the AWS account.

Verification evidence:
- `cdk synth` all 4 stacks → clean.
- 39 unit/synth tests pass (28 in `venv/`, 11 in `infra/.venv/`).
- Live `POST /query` (in-corpus): 200, grounded answer with `[1]` citation, 3 sources from `refund-policy.md`, confidence 0.67, conversation_name="Refund window for monthly plans", latency 3.4s. ✅
- Multi-turn (3 user prompts on same session_id): conversation_name only on turn 1; turns 2–3 returned null as expected. ✅
- `GET /conversations/{sid}` after 3 turns: returns 6 messages in chronological order, USER/ASSISTANT alternating, native `conversational` shape (NOT msgpack). ✅
- `GET /conversations`: returns the persisted conversation with `conversation_name` + `created_at`. ✅
- `scripts/smoke_test.py`: 5/5 PASS (added a 5th check for `/conversations`). ✅
- `tests/eval/run_eval.py`: source-match 100% (6/6 in-corpus), mean in-corpus confidence 0.813, off-corpus confidence clamped to 0.200. **Identical metrics to Phase 6**, confirming no behavioral regression through the new AgentCore Runtime + Memory stack. ✅

### 21.5 Live-deploy incidents and corrective actions

Three issues surfaced during live deploy; all fixed.

**Incident A — Cognito UserPoolDomain reserved-prefix.** Initial `domain_prefix = f"rag-aws-{account[-6:]}"` failed: Cognito rejects prefixes containing `aws`, `amazon`, or `cognito`. Fixed to `ragkb-{account-suffix}` in [auth_stack.py:78-79](infra/stacks/auth_stack.py#L78-L79). One-line change; redeploy succeeded.

**Incident B — AgentCore Runtime is arm64-only.** Sub-agent A set `platform=ecr_assets.Platform.LINUX_AMD64`; AgentCore rejected the image with `Supported platforms: [arm64]`. Fixed to `LINUX_ARM64` in [agent_stack.py:52](infra/stacks/agent_stack.py#L52). **Generalizable lesson**: AgentCore Runtime is arm64-only. Bake this into any future `CfnRuntime` work. (Verified post-fix: docker buildx builds linux/arm64 cross-arch from my x86 Mac via Docker Desktop's emulation — slower first build, but works.)

**Incident C — DynamoDB `Decimal` not JSON serializable.** `created_at` is written as `int(time.time())` by the agent, but DDB returns it as `Decimal` on read. The `/conversations` handler `json.dumps(items)` raised `TypeError: Object of type Decimal is not JSON serializable`, yielding a 500. Fixed by explicit `int(i["created_at"])` coercion in [lambda/conversations.py:34-41](lambda/conversations.py#L34-L41). **Generalizable lesson**: anything read via `boto3.resource("dynamodb").Table(...).query(...)` returns Decimal for numbers; coerce at the boundary before `json.dumps`.

**Known limitation — test-user password sync on secret rotation**: the AwsCustomResource that calls `AdminSetUserPassword` keys off CFN dynamic refs (`secret_value.unsafe_unwrap()`), which are template-static even when the underlying Secrets Manager value rotates. If the password generator config changes between deploys (e.g., changing `exclude_punctuation`), the secret is regenerated but the live user's password is NOT re-synced. **Workaround** (one-time, after such a change): `aws cognito-idp admin-set-user-password --user-pool-id $UPID --username demo --password "$(aws secretsmanager get-secret-value --secret-id $PWD_ARN --query SecretString --output text)" --permanent --region us-east-1`. **Proper fix (not yet applied)**: bind the custom-resource's `physical_resource_id` to a hash that changes when the secret rotates, or force-rerun on every deploy.

### 21.6 What's improved over the AWS sample

| Aspect | Sample (iteration 3) | This implementation |
|---|---|---|
| AgentCore Runtime+Memory IaC | `agentcore` CLI out-of-band + SSM glue | CDK L1 (`CfnRuntime` + `CfnMemory`), 100% `cdk diff`-able |
| Memory event shape | LangGraph msgpack blobs | Native `conversational` payload, console-introspectable |
| `list_events` pagination | Single page, silent truncation on long conversations | Full `nextToken` loop |
| `invoke_agent_runtime` payload | `payload=str` (boto3 spec says bytes) | `payload=...encode("utf-8")` |
| `runtimeSessionId` | Auto-generated; app session_id only in payload | App session_id explicitly passed (padded to ≥33 chars) |
| LangGraph dependency | Required (`langgraph`, `langgraph-checkpoint-aws`, `langchain-aws`) | None — pure boto3 |

### 21.7 Cost incurred this phase

~$0.40 total: docker push to ECR (~$0.05), KB ingestion (~$0.001), ~15 Haiku 4.5 calls across smoke + eval + multi-turn test (~$0.08), 4 fresh stack deploys (rounding). AgentCore Runtime container while up: ~$0.20–0.50/day idle.

Cumulative project total still well under $2 of the $20 budget. **Run `cdk destroy AgentStack StorageStack AuthStack ApiStack --force` after this session if not actively testing** to zero idle cost.

---

## 22. Phase 9 — what we did

Elected from §19's optional-extension menu on 2026-05-25. Brief: "streaming responses to Streamlit + upload/ingestion endpoint for new documents." Done as two sub-phases (9a, 9b) — sequential sub-agents, then two-commit split, then a single live redeploy + verification pass.

### 22.1 Locked architectural decisions (do NOT relitigate)

| Area | Choice | Rationale |
|---|---|---|
| Streaming transport | **Lambda Function URL + SSE** (`InvokeMode=RESPONSE_STREAM`) | REST API can't stream; Function URL is the AWS-native path for Python Lambda response streaming. Lives alongside `/query`, doesn't replace it. |
| Streaming runtime | **AWS Lambda Web Adapter + FastAPI/uvicorn** (Python image) | Python Lambda has NO native response streaming — Node-only as of May 2026. LWA + a long-running ASGI server inside the container is the only supported path. The streaming Lambda uses a different base image (`python:3.12-slim`) from the buffered Lambda (`public.ecr.aws/lambda/python:3.12`). |
| Streaming model call | **`bedrock-runtime.invoke_model_with_response_stream`** directly | AgentCore Runtime's `InvokeAgentRuntime` API is buffered-only as of May 2026 (no streaming counterpart). The streaming Lambda calls Bedrock directly and writes AgentCore Memory + DDB itself after the stream completes — keeps behavior byte-identical to `/query`. |
| Streaming auth | **In-handler JWT verify** via Cognito JWKS (`pyjwt[crypto]==2.13.0`, `PyJWKClient`) | Function URL `auth_type=NONE`; Streamlit cannot SigV4-sign. Explicit `token_use=="id"` check so an access-token can't be substituted. |
| Streaming SSE protocol | `meta` → `token`×N → `sources` → `done` (named SSE events, JSON `data`) | Mirrors `QueryResponse` shape so consumers don't learn two schemas. INSUFFICIENT_CONTEXT handled by appending canned text + clamping confidence in `done`; client strips marker prefix. |
| Endpoint coexistence | Add `/query-stream` alongside `/query`; keep `/query` for smoke + eval | Smoke/eval scripts stay deterministic. Streamlit toggles between paths per user choice. |
| Upload mechanism | **Presigned S3 PUT URL** (SigV4, 15-min TTL, signed `ContentType`) | No Lambda payload limit, no Lambda compute cost per byte. Client PUTs directly to S3. |
| Ingestion trigger | **Explicit POST /ingest** with `client_token` idempotency; **GET /ingest/{job_id}** polling | Gives the user feedback (progress bar) and explicit control over when ingestion cost is incurred. NOT auto-triggered by S3 events. |
| Scope | **Shared corpus** — all authenticated users see all uploads | Single KB data source can't easily filter per user. Per-user namespacing is on the README hardening list. |
| Upload key shape | `uploads/{yyyy-mm-dd}/{uuid4-hex}-{sanitized-filename}` | Date prefix for humans browsing the bucket; uuid for collision-resistance. Sanitization: `[^A-Za-z0-9._-]+` → `-`, lowercase, ≤100 bytes preserving extension. |
| File whitelist | 9 extensions matching what Bedrock KB natively supports (`.txt .md .html .htm .csv .pdf .doc .docx .xls .xlsx`) with strict MIME-vs-extension cross-check | JPEG/PNG omitted — KB supports them but only via the multimodal pipeline. |

### 22.2 File topology (this phase)

**Created**:
- [lambda_stream/](lambda_stream/) — entirely new directory: `Dockerfile` (LWA base), `requirements.txt`, `stream_app.py` (FastAPI app with SSE handler), `schemas.py` (copy of `lambda/schemas.py` because Docker contexts are isolated)
- [tests/test_stream.py](tests/test_stream.py) — 9 unit tests
- [lambda/uploads.py](lambda/uploads.py) — 3 handlers + helpers
- [tests/test_uploads.py](tests/test_uploads.py) — 22 unit tests

**Heavy edits**:
- [infra/stacks/api_stack.py](infra/stacks/api_stack.py) — added second `DockerImageFunction` + Function URL with `RESPONSE_STREAM` + CORS for `http://localhost:8501` + all the IAM (Phase 9a); added 3 REST routes + IAM for `s3:PutObject` on `uploads/*` + `bedrock:StartIngestionJob`+`GetIngestionJob` (Phase 9b)
- [lambda/app.py](lambda/app.py) — extended route table to 7 endpoints; added `_claims` helper; added 3 new env vars (DOCS_BUCKET, KB_ID, DATA_SOURCE_ID)
- [lambda/schemas.py](lambda/schemas.py) — 5 new pydantic models (`UploadRequest`, `UploadResponse`, `IngestRequest`, `IngestResponse`, `IngestionStatistics`, `IngestionStatusResponse`)
- [streamlit_client/app.py](streamlit_client/app.py) — SSE consumer + "Stream responses" toggle (Phase 9a); sidebar Upload section with `st.file_uploader` + `st.status` progress (Phase 9b); INSUFFICIENT_CONTEXT marker-strip logic
- [scripts/smoke_test.py](scripts/smoke_test.py) — added 6th check (SSE) + 7th check (upload+ingest+follow-up-query, 90s)
- [tests/test_synth.py](tests/test_synth.py) — added 7 new synth assertions (2 Lambdas, Function URL with RESPONSE_STREAM + CORS, streaming Lambda env, 7 routes, IAM on `s3:PutObject` + bedrock ingestion); updated legacy-vars guard to identify the buffered Lambda by `AGENTCORE_RUNTIME_ARN`
- [tests/conftest.py](tests/conftest.py) — added `lambda_stream/` to `sys.path`, added `USER_POOL_ID` + `USER_POOL_CLIENT_ID` + `DOCS_BUCKET` + `DATA_SOURCE_ID` env

**Deleted**: nothing.

### 22.3 Live verification (2026-05-25 late afternoon)

Single `cdk deploy --all` from a destroyed state. Wall-clock ~7 minutes (StorageStack ~70s, AuthStack ~30s, AgentStack arm64 cross-build ~3min, ApiStack 2 docker images + 7 routes ~70s). Cost: ~$0.15 (docker pushes + ingest + ~20 Bedrock calls across all tests).

Verification evidence:
- `cdk synth --all` clean throughout development.
- **76 unit/synth tests pass** (59 in `venv/` + 17 in `infra/.venv/`).
- `scripts/smoke_test.py` → **7/7 PASS** (after one-time password sync, see §22.4).
- `tests/eval/run_eval.py` baseline: still 100% source-match in-corpus, mean confidence 0.813, off-corpus clamped to 0.200 — **no regression vs Phase 6/8**.
- **Multi-turn live test** (`/tmp/multi_turn_test.py`): three turns on a single `session_id` produced exactly 1 DDB row (with `conversation_name` set on turn 1) + 3 AgentCore Memory events (USER + ASSISTANT payload, native `conversational` shape). Confidence scoring discriminated cleanly (0.854 → 0.601 → 0.200 clamped).
- **Streaming live test** (`/tmp/test_stream_sse.py`): three streaming sessions captured. First-token latency 1.16–1.33s. Full SSE protocol observed in order: meta → token×N → sources → done. INSUFFICIENT_CONTEXT marker triggered the canned-answer rewrite + 0.200 clamp.
- **Upload + ingest + retrieval live test** (`/tmp/upload_and_query_test.py`): uploaded a fictional `engineering-roadmap-*.md` (~700B) via presigned PUT. POST /ingest returned `job_id`; GET /ingest/{job_id} reached COMPLETE in ~6s with `scanned=8, indexed=1`. Three follow-up `/query` calls against unique factoids from the new doc — all three cited the new doc as the top source. Interesting observation: when asked for a (synthetic) credential phrase embedded in the doc, Claude **refused to surface it** as a safety reflex.

### 22.4 Live-deploy incidents and corrective actions

One issue hit on the first call after the fresh deploy — already documented in [[feedback-aws-phase8-gotchas]] (Gotcha #4) but worth re-stating in the Phase 9 record because it's a recurring trap.

**Incident A — `AwsCustomResource` + `SecretValue.unsafe_unwrap()` does NOT re-sync on regenerated secret.** Fresh deploy → secret regenerated → Cognito custom resource's `parameters={"Password": secret_value.unsafe_unwrap()}` rendered the same template-static reference → `AdminSetUserPassword` did NOT re-fire → smoke test `admin-initiate-auth` failed with `NotAuthorizedException`. **Workaround applied (one-line)**:
```bash
aws cognito-idp admin-set-user-password --region us-east-1 \
  --user-pool-id "$UPID" --username demo --permanent \
  --password "$(aws secretsmanager get-secret-value --secret-id "$PWD_ARN" --region us-east-1 --query SecretString --output text)"
```
Documented prominently in [README.md §10.1](README.md) so a reviewer doing a fresh deploy follows it as part of the runbook. Proper CDK fix (binding the custom resource's `physical_resource_id` to a hash of the secret) is on the production-hardening list — not applied this phase.

No other live-deploy gotchas hit. The 4 Phase 8 gotchas in [[feedback-aws-phase8-gotchas]] (arm64-only Runtime, Cognito reserved prefix, DDB Decimal, custom-resource non-rotation) are already accounted for in the source.

### 22.4a Caveats for a future Claude session

Three things to know going into the next session that the current source + commits don't make obvious:

1. **The test-user password sync ritual must run after every fresh `cdk deploy`** (or after any change to the secret-generator config). The AwsCustomResource doesn't re-fire on regenerated secret values — see [§7 #4 of README.md](README.md) for the one-liner. Smoke + eval fail with `NotAuthorizedException` on the very first call after deploy if you skip it.

2. **LWA cold-start is real and only on the first streaming call**. The Python ASGI server inside the LWA container needs ~4–6s to import + bind on the first invocation after a deploy or after Lambda has scaled to zero. Subsequent calls land in 1–1.5s. If a reviewer hits the streaming endpoint cold and thinks something is broken, this is the cause — confirm with a second call. Pre-warming via a single `/health` (no auth) is NOT effective because /health is on a different Lambda (the buffered handler).

3. **AgentCore Runtime is buffered-only and will likely stay that way for the near future.** Don't waste time looking for `InvokeAgentRuntime`-with-streaming; it doesn't exist as of May 2026. The Phase 9a streaming Lambda intentionally bypasses AgentCore Runtime and writes Memory + DDB itself after the stream — that duplication (Memory + DDB write logic in both `agent/rag.py` and `lambda_stream/stream_app.py`) is a deliberate consequence, not tech debt to consolidate. If/when AgentCore exposes streaming, the duplication can collapse.

4. **The Streamlit `secrets.toml` must have EVERY `[auth]` key populated** for `st.login()` to start. Any field left as the placeholder string causes Streamlit to abort during initial import (not on first user click) with a non-obvious error. **Obsoleted as a manual task in Phase 10** (2026-05-25) — `./scripts/run_streamlit.sh` now auto-syncs the file from live CloudFormation + Secrets Manager on every launch. Don't hand-edit `secrets.toml`; just use the launcher.

### 22.5 What's new vs. the Phase 8 baseline

| Aspect | Phase 8 state | Phase 9 state |
|---|---|---|
| Endpoints | 4 (REST: health, query, conversations, conversations/{id}) | **7 REST + 1 Function URL** (added documents, ingest, ingest/{id}, query-stream) |
| Lambdas | 1 buffered | **2 (buffered RIC + streaming LWA)** |
| Auth surface | APIGW Cognito authorizer only | APIGW Cognito authorizer + **in-handler JWKS verify** (Function URL) |
| Streaming UX | None — `/query` blocks ~3s | **Sub-1.3s first-token** SSE via Streamlit toggle |
| Runtime ingestion | Operator scripts only | **POST /documents + /ingest** from authenticated client |
| Streamlit UI | Chat + sidebar history | + Stream toggle + File uploader + status panel |
| Smoke checks | 5 | **7** |
| Unit + synth tests | 39 | **76** (37 unit + 13 synth Phase 8 baseline → 59 unit + 17 synth) |

### 22.6 Cost incurred this phase

~$0.15 total: one fresh `cdk deploy --all` (docker push for 2 images + arm64 cross-build), KB ingestion ×2 (bootstrap + one custom upload), ~25 Claude Haiku 4.5 calls across smoke + eval + multi-turn + streaming + upload tests. AgentCore Runtime container while up: ~$0.20–0.50/day idle.

Cumulative project total still well under **$2.50** of the $20 budget. **Run `cdk destroy --all --force` after this session if not actively testing** to zero idle cost.

---

## 23. Phase 10 — developer-loop polish (2026-05-25, late session)

Short session. No infra, no Bedrock, no AWS billable changes. The two-command developer loop Miguel wanted was on his head: "after `cdk deploy`, I should just run Streamlit — no secrets editing, ever."

### 23.1 The symptom

Miguel opened Streamlit and clicked **Log in with Cognito** → generic "authentication error" on the page. Root cause was diagnosed live in this session: `streamlit_client/.streamlit/secrets.toml` was frozen at Phase 8 IDs. The Phase 9 `cdk destroy --all` + redeploy had rotated every Cognito and API ID:

| Field | Stale (pre-redeploy) | Live (post-redeploy) |
|---|---|---|
| `UserPoolId` | previous deploy value | rotated |
| `UserPoolClientId` | previous deploy value | rotated |
| App Client secret | old | refetched from Secrets Manager |
| `ApiUrl` | previous deploy value | rotated |
| `[stream]` section | missing entirely | now present |

Streamlit was POSTing the OIDC discovery request to a user pool that no longer exists → Cognito 404 → Streamlit surfaces it as "authentication error". No AWS-side fix needed; just the secrets file.

### 23.2 The fix

New file: [scripts/run_streamlit.sh](scripts/run_streamlit.sh) (bash, +x, ~70 lines). It:
- Calls `aws cloudformation describe-stacks` against `AuthStack` + `ApiStack` and extracts `UserPoolId`, `UserPoolClientId`, `UserPoolClientSecretArn`, `ApiUrl`, `StreamFunctionUrl` via JMESPath. **Does NOT read `cdk-outputs.json`** — that file is only written when `--outputs-file` is passed to `cdk deploy`, easy to forget. Pulling from CFN directly makes the script depend only on "stacks are deployed."
- Calls `aws secretsmanager get-secret-value` for the App Client secret.
- Preserves an existing `cookie_secret` across reruns (grep + sed extraction) so live Streamlit session cookies survive. Mints a fresh 32-byte hex one only if missing or still set to the placeholder string.
- Writes `streamlit_client/.streamlit/secrets.toml` with all `[auth]` + `[api]` + `[runtime]` + `[stream]` keys populated.
- `exec`s `./venv/bin/python -m streamlit run streamlit_client/app.py "$@"` (or `streamlit run …` fallback if `venv/` isn't there). Passes through any extra CLI args.
- Fails loudly if any required output is missing: `error: output 'UserPoolId' not found on stack 'AuthStack'. Did you run 'cd infra && cdk deploy --all'?`

Miguel's new developer loop is now exactly:
```bash
cd infra && env -u PYTHONPATH cdk deploy --all --require-approval never && cd ..
./scripts/run_streamlit.sh
```

No `--outputs-file` flag needed. No manual `secrets.toml` editing, ever.

### 23.3 Docs updated

- [README.md](README.md) §10.0 — new "TL;DR — returning developer, two-command loop" subsection at the top of §10.
- [README.md](README.md) §10.4 — full rewrite of "Run the local Streamlit client" to describe the launcher; old multi-line manual cheat-sheet for filling each `secrets.toml` key is removed.
- [README.md](README.md) §7 — added a 7th gotcha: "redeploy rotates every ID → `secrets.toml` will be stale → use the launcher." Documents the exact symptom Miguel hit so a future reviewer/Claude recognizes the pattern instantly.
- [SESSION_HANDOFF.md](SESSION_HANDOFF.md) §22.4a #4 — annotated as obsoleted by Phase 10; the launcher replaces the "every key populated" checklist.

### 23.4 Verification

- Dry-ran the launcher locally: `secrets.toml` rewritten was byte-identical to the hand-fixed version that got login working. `diff` clean.
- Cognito sanity checks done before declaring success: OIDC discovery doc on the current `UserPoolId` resolves; Hosted UI domain `ragkb-{account-suffix}` exists; App Client has `http://localhost:8501/oauth2callback` whitelisted, `code` flow, `openid email profile` scopes. The "Log in with Cognito" button works end-to-end after the secrets sync (Miguel confirmed live).
- `chmod +x scripts/run_streamlit.sh` applied.

### 23.5 Cost incurred this phase

$0. Read-only AWS calls (CFN describe-stacks + Secrets Manager get-secret-value). No Bedrock, no Lambda invocations, no S3 puts.

### 23.6 Things future-Claude should know about Phase 10

- The launcher is **the** developer entry point. If Miguel reports any auth/login confusion, **first check that he's running `./scripts/run_streamlit.sh` rather than `streamlit run …` directly**. The latter will use whatever stale `secrets.toml` is on disk.
- The launcher is intentionally region-aware via `${AWS_REGION:-us-east-1}` but the rest of the project pins `us-east-1`. Don't generalize without checking CDK pin in [infra/app.py](infra/app.py).
- `cookie_secret` preservation matters: if you rewrite the launcher to always regenerate it, you'll invalidate any in-browser Streamlit session and Miguel has to re-log-in. Annoying. Keep the grep-existing-then-mint-if-missing pattern.
- The `--outputs-file ../cdk-outputs.json` flag in §10.1 of the README is still useful (lots of scripts in `scripts/` and `tests/eval/` read from it). The launcher is the **one** exception that avoids the dependency. Don't refactor the other scripts onto CFN describe-stacks unless asked — it's a perf trade-off (CFN API is 200–500ms per stack, jq on a local file is microseconds).
- The README's §1 architecture diagram still references `secrets.toml` as a config artifact — that's accurate, the file still exists; only the mechanism for filling it changed.

### 23.7 Memory updates

- New feedback memory [[feedback-streamlit-secrets-auto-sync]] codifying "always launch via `./scripts/run_streamlit.sh`; never hand-edit `secrets.toml`."
- [[project-phase8-9-extensions-done]] left alone (Phase 10 isn't an extension — it's developer-experience polish on existing functionality).

---

## 24. Phase 11 — consistency review + password-sync hardening (2026-05-25 late, post-Phase-10)

Short session, two outputs: closed a long-running doc-vs-reality drift on PLAN.md, and root-caused + fixed the test-user password-sync trap that had haunted every fresh deploy since Phase 8.

### 24.1 The consistency review

Miguel asked for a sanity pass: are PLAN.md, SESSION_HANDOFF.md, README.md, the original brief, the memory files, and the code on disk all consistent with one another? After reading them end-to-end:

- **README.md ↔ SESSION_HANDOFF.md ↔ code tree ↔ memory files**: all aligned. Phase-10 state is reflected uniformly.
- **Brief compliance**: all "minimum strong submission" capabilities present; 5 of 8 optional extensions delivered + the Cognito JWT bonus; no constraint violations (within $20 budget, no OSS, no SageMaker, CDK in Python, region-pinned).
- **PLAN.md was severely stale**. It was frozen at the original Phase-1-through-7 vision:
  - Said "AgentCore: out of scope (README only)" while AgentCore Runtime + Memory are load-bearing.
  - Described auth as "API Gateway API key + Usage Plan" (removed in Phase 8).
  - Architecture diagram showed 2 stacks; reality is 4.
  - Listed `scripts/rotate_api_key.py` (deleted in Phase 8).
  - Repo layout omitted `agent/`, `lambda_stream/`, `lambda/conversations.py`, `lambda/uploads.py`, `auth_stack.py`, `agent_stack.py`.

**Fix**: full rewrite of [PLAN.md](PLAN.md) (~430 → ~280 lines) to describe the current 4-stack architecture, all 10 phases as a status table, the Lambda-backed password-sync fix (§24.2), and the optional-extensions ledger.

### 24.2 The password-sync root cause + fix

The historical narrative in [[feedback-aws-phase8-gotchas]] #4 + [README §7 #4](README.md) was: "`AwsCustomResource` + `secret_value.unsafe_unwrap()` does not re-fire when the secret rotates → live password and secret diverge silently → `NotAuthorizedException` on first auth." The workaround was a one-line `aws cognito-idp admin-set-user-password` after every fresh deploy.

That narrative was **wrong about the mechanism**. First attempt to "fix" the bug: bind the AwsCustomResource's `physical_resource_id` to the secret ARN so the CR re-fires on fresh deploy. Deployed clean. Smoke test still failed with `NotAuthorizedException`.

Inspected the synthesized CFN template. The `Create` property of `Custom::AWS` contained the JSON string:
```
"Password":"{{resolve:secretsmanager:<ARN>:SecretString:::}}"
```
Per [AWS docs](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/dynamic-references-secretsmanager.html): **CFN explicitly does NOT resolve `secretsmanager` dynamic references inside Custom Resource properties.** The literal `{{resolve:...}}` string was being passed verbatim to `AdminSetUserPassword` as the password — every deploy, not just on second deploy. The historical workaround was masking the real bug, not patching around a rotation edge case.

**Proper fix** ([infra/stacks/auth_stack.py:113-175](infra/stacks/auth_stack.py#L113-L175)): replace `AwsCustomResource` with a small Lambda-backed Custom Resource. ~30 lines of inline Python:
- `SetPasswordFn` (`aws_lambda.Function`, Python 3.12, 30s timeout, log_retention=1 week)
- Lambda body reads the secret via `boto3.client("secretsmanager").get_secret_value(SecretId=props["SecretArn"])`, then calls `cognito.admin_set_user_password(...)`, then PUTs SUCCESS to the CFN response URL.
- `CustomResource` with properties `(SecretArn, UserPoolId, Username)`. CFN sends `RequestType=Create`/`Update` on every property diff — and the `SecretArn` value changes whenever the secret is regenerated, so the Lambda re-runs on fresh deploys.
- IAM: `secret.grant_read(set_password_fn)` + `cognito-idp:AdminSetUserPassword` on the user pool ARN.

### 24.3 Live verification (drift-repair test)

End-to-end proof, performed sequentially against the deployed AuthStack:

1. `aws cognito-idp admin-set-user-password ... --password "Corrupted-Password-9999!"` — deliberately desynced the live Cognito password from the secret value.
2. `python scripts/get_id_token.py` → `NotAuthorizedException: Incorrect username or password.` ✓ (confirms drift)
3. `aws lambda invoke --function-name AuthStack-SetPasswordFn... --payload <synthetic CFN Create event>` → `StatusCode: 200`. Lambda read the secret, called `AdminSetUserPassword`, PUT response.
4. `python scripts/smoke_test.py` → **7/7 PASS** — with no manual `admin-set-user-password` between steps 3 and 4.

This proves the Lambda body works against the live system. On a fresh `cdk destroy --all` + redeploy, CFN will fire the Lambda with `RequestType=Create` and the password will sync automatically — the workaround documented in [§7 of README](README.md) and [§22.4a of this handoff](SESSION_HANDOFF.md) is now unneeded.

### 24.4 Other doc edits this phase

- **[README §1.2](README.md)** — sharpened the AgentCore Runtime rationale. Old framing: "adds runtime memory + managed scaling + console observability." New framing acknowledges: single-tool RAG doesn't *need* AgentCore; the choice was to demonstrate the primitive + ~$0.20–0.50/day idle + adds ~100–300ms hop. Acceptable for demo/interview; pure Lambda is the cheaper choice for real single-tool production.
- **[README §9 hardening list](README.md)** — added the `agent/rag.py` ↔ `lambda_stream/stream_app.py` duplication risk + suggested a CI lint as the production remediation. The duplication is currently invisible in the docs except for a single line in §1.2.
- **[README §7 #4](README.md)** — rewritten with the real CFN-dynamic-ref root cause + cross-link to AWS docs. The legacy "rotation doesn't re-fire" framing is gone.
- **[README §10.1](README.md)** — removed the mandatory `admin-set-user-password` step. The one-liner is preserved as a stop-gap reference but is no longer part of the deploy runbook.

### 24.5 Memory updates this phase

- [[feedback-aws-phase8-gotchas]] gotcha #4 — annotated as resolved in Phase 11 with a pointer to this section + the new auth_stack pattern. The "Workaround" + "Proper fix (not yet applied)" subsections are obsolete; the Lambda-backed CR IS the proper fix.

### 24.6 Live state (end of Phase 11)

All 4 stacks deployed in `us-east-1`. AuthStack was redeployed once (Phase 11.2 fix); the other 3 are from the earlier Phase-11 full-redeploy.

`cdk-outputs.json` was regenerated from `aws cloudformation describe-stacks` after the per-stack AuthStack redeploy (single-stack deploy overwrites the outputs file).

### 24.7 Cost incurred this phase

~$0.30 total: 4-stack full redeploy from a destroyed state (docker push + arm64 cross-build dominated), single AuthStack re-update with the Lambda CR fix, ingestion of 6 docs, ~20 Bedrock calls across smoke + drift-repair test. AgentCore Runtime is up and idling at ~$0.20–0.50/day until `cdk destroy --all`. Cumulative project spend across 11 phases is still under **$3** of the $20 budget.

### 24.8 What future-Claude should know about Phase 11

- The password-sync workaround in older docs is now obsolete. If a future session encounters references to "you must run `aws cognito-idp admin-set-user-password` after a fresh deploy", point them at [auth_stack.py:113-175](infra/stacks/auth_stack.py#L113-L175) — that's the actual fix; the workaround is a stop-gap only.
- `unsafe_unwrap()` of a Secrets Manager secret value will NOT work inside any `Custom::AWS` resource property. If you ever need to pass a secret value to a custom resource, use a Lambda-backed Custom Resource that reads the secret itself via boto3.
- PLAN.md is now current (Phase 11) and aligned with README + SESSION_HANDOFF. Future phases should update PLAN.md alongside SESSION_HANDOFF.md to keep this from drifting again.

---

## 25. Phase 12 — production-like deploy (2026-05-25 evening)

Elected per [[project-phase12-production-deploy-planned]]. Goal: ship a publicly reachable, prod-shaped version of the system without flipping any of the "real-traffic" knobs (no CI/CD, no VPC, no per-user isolation, no multi-account, no MFA, no billing alarm — all explicitly declined by Miguel as out-of-scope for "production-like, not production").

### 25.1 Locked decisions (Phase 12)

| Area | Choice | Rationale / source |
|---|---|---|
| Streamlit hosting | **Streamlit Community Cloud** at `https://rag-aws-kb.streamlit.app` (free, public, GitHub-connected) | Miguel reported AWS App Runner stopped accepting new customers 2026-04-30; ECS Fargate + ALB was overkill (~$25/mo idle vs free). |
| Custom subdomain | `rag-aws-kb.streamlit.app` (set in Streamlit Cloud UI) | Streamlit Cloud free tier doesn't support custom DNS even if you own one in Route 53. |
| Cognito callbacks | **Both** localhost + Streamlit Cloud URLs registered on the App Client | Lets `./scripts/run_streamlit.sh` keep working against the prod-shaped stacks. No security cost — localhost URL is only useful from your machine. |
| Throttling | **Method-level throttling on APIGW** — per-method rate/burst caps + stage backstop 20/40 | Replaces the API-key UsagePlan removed in Phase 8 without re-introducing static keys. /ingest has tightest cap (5/10) since it triggers KB embedding cost. |
| Billing alarm | None (declined) | Cumulative spend across 12 phases still under $3 of $20 budget. |
| RemovalPolicy | DESTROY (kept per Miguel's ask) | Miguel wants to be able to `cdk destroy --all` on demand. Accepted trade-off: re-paste Streamlit Cloud secrets after each redeploy. |
| `autoDeleteObjects` | True on docs bucket (kept) | Same reason as above. |
| Demo user | Kept (no MFA, no recovery — same as Phase 8) | Not real traffic, single fixture user is fine. |
| Stack topology | Same 4 stacks, same names, single AWS account, single region `us-east-1` | "Replace dev with prod" — no parallel naming. |

### 25.2 Files (this phase)

**Created**:
- [scripts/print_streamlit_cloud_secrets.py](scripts/print_streamlit_cloud_secrets.py) — reads live CFN outputs + Secrets Manager, prints TOML to stdout for paste into Streamlit Cloud Secrets editor. Persists `cookie_secret` in `.streamlit_cloud_cookie_secret` (gitignored) so sessions survive re-pastes.

**Edited**:
- [infra/stacks/api_stack.py](infra/stacks/api_stack.py) — added `_STREAMLIT_CLOUD_ORIGIN`/`_ALLOWED_ORIGINS` constants; method-level throttling via `StageOptions.method_options` on all 7 routes + stage backstop; multi-origin REST CORS preflight; Function URL CORS now allows the Streamlit Cloud origin.
- [infra/stacks/auth_stack.py](infra/stacks/auth_stack.py) — App Client `callback_urls` + `logout_urls` now include both `http://localhost:8501/...` and `https://rag-aws-kb.streamlit.app/...`.
- [tests/test_synth.py](tests/test_synth.py) — Function URL CORS assertion updated to expect both origins.
- [.gitignore](.gitignore) — added `.streamlit_cloud_cookie_secret`.
- [README.md](README.md) — new §10.6 "Production-like deployment (Streamlit Community Cloud)" + updated §9 to mark APIGW throttling as implemented.
- [SESSION_HANDOFF.md](SESSION_HANDOFF.md) — this section.

**Not changed**: `agent/`, `lambda/`, `lambda_stream/`, `streamlit_client/app.py`. The Streamlit client reads `st.secrets` the same way whether running locally or on Streamlit Cloud — no code change needed.

### 25.3 Pre-deploy verification

- `cdk synth --all` → clean (4 stacks).
- `cdk diff AuthStack`: 2 callback URLs + 2 logout URLs added (as planned).
- `cdk diff ApiStack`: stage backstop throttle + 7 per-method throttle entries + multi-origin CORS preflight templates + Function URL CORS multi-origin (as planned).
- `cdk diff AgentStack` / `StorageStack`: no differences (untouched, as planned).
- **Tests: 76/76 PASS** — 59 unit (in `venv/`) + 17 synth (in `infra/.venv/`).
- Repo audit before GitHub push: no AKIA/AIza/sk-/ghp_/xoxb tokens in tracked files; `.env`, `secrets.toml`, `cdk-outputs.json` all gitignored; `legacy/.streamlit/secrets.toml` is tracked but contains only commented placeholders (safe).

### 25.4 Live verification (2026-05-25 evening — verified)

End-to-end live walkthrough on **https://rag-aws-kb.streamlit.app**, confirmed by Miguel:

- `cdk deploy --all` against existing stacks: all 4 stacks `UPDATE_COMPLETE` (StorageStack + AgentStack: no diff; AuthStack + ApiStack: in-place updated). Same `UserPoolId` / `ApiUrl` / `StreamFunctionUrl` as end of Phase 11 — no ID rotation. ✅
- 7/7 smoke tests pass against the deployed REST + SSE surface (throttling + multi-origin CORS introduced no regressions). ✅
- Public GitHub repo created (`rag-aws-kb`) and pushed. ✅
- Streamlit Community Cloud app connected to `streamlit_client/app.py`, Python 3.12, custom subdomain `rag-aws-kb` reserved. ✅
- Secrets pasted into Streamlit Cloud Secrets editor via `python scripts/print_streamlit_cloud_secrets.py | pbcopy`. ✅
- **Browser walkthrough on the public URL** (Miguel's report): Cognito Hosted UI login as `demo` succeeded (password fetched from the Secrets Manager console — the CLI one-liner in [README §10.4](README.md) does the same); chat works with both streaming and non-streaming toggles; file upload + ingestion completes; sidebar past-conversations renders and replays correctly. ✅
- Leaked Gemini key in legacy `.env` revoked on Google AI Studio. ✅

The system is now publicly reachable and end-to-end functional at the prod URL. No outstanding Phase 12 work.

### 25.5 Things future-Claude should know about Phase 12

- **App Runner is unavailable for new customers since 2026-04-30.** Don't propose it. ECS Fargate + ALB is the AWS-native alternative if Miguel ever wants to host the Streamlit container inside AWS; ~$25/mo idle vs Cloud's free.
- **Streamlit Cloud secrets are dashboard-managed**, no public API. After every `cdk destroy --all` + redeploy, the user re-runs `scripts/print_streamlit_cloud_secrets.py` and pastes the TOML manually. There is no equivalent of the `run_streamlit.sh` auto-sync for Streamlit Cloud. This is the cost of `RemovalPolicy.DESTROY`.
- **Cookie secret persistence**: the print-secrets script writes/reads `.streamlit_cloud_cookie_secret` (gitignored) to keep the cookie stable across re-pastes. Don't delete this file casually — losing it invalidates all live Streamlit Cloud sessions.
- **The Cognito App Client now has 2 callback URLs.** This is fine — Cognito picks the one matching the `redirect_uri` query param at login time. If you ever need to add a third (e.g., a staging environment), keep localhost so local dev keeps working.
- **Method throttling caps are account-wide per-method**, not per-caller. If you ever see `429 Too Many Requests` in logs from legitimate traffic, raise the limits in `api_stack.py` — but for a demo with one user, the current caps are very generous (10 req/sec sustained on `/query`).
- **No new AWS resources** were added in Phase 12 — only API Gateway stage-level method settings + Cognito App Client property edits + Function URL property edit. So no new ARNs to track; existing outputs in `cdk-outputs.json` are still the canonical reference.

### 25.6 Cost incurred this phase

~$0.02 — `cdk diff` + the in-place `cdk deploy --all`. No new resources beyond stage settings + Cognito App Client property updates + Function URL property update. Cumulative project spend across 12 phases is still under **$3** of the $20 budget. AgentCore Runtime continues to idle at ~$0.20–0.50/day; Streamlit Cloud is free.

### 25.7 Memory updates this phase

- New project memory [[project-phase12-production-deploy-done]] saved (replaces the now-deleted `project-phase12-production-deploy-planned`).
- New feedback memory [[feedback-aws-apprunner-unavailable]] codifying the App Runner cutoff so future projects default to Streamlit Cloud / ECS Fargate.
- MEMORY.md index refreshed to point at the new files.

---

## 26. Phase 13 — UI redesign + sources-on-reload persistence (2026-05-26)

Two distinct improvements landed in this session: a substantial Streamlit UI redesign (no infra), plus a backend change (`AgentStack` + `ApiStack` redeployed) so per-turn `sources` + `confidence` + `latency_ms` + `model_id` persist in AgentCore Memory and surface when a past conversation is reloaded. Triggered by Miguel testing the UI and noticing two UX bugs after the redesign was already in place.

### 26.1 Locked decisions (Phase 13)

| Area | Choice | Rationale |
|---|---|---|
| UI app name | **"Knowledge Base"** (generic) | Miguel asked to remove all Acme references from the UI. Sample-doc content can keep referencing Acme — only the UI strings changed. |
| Theme | `[theme.light]` + `[theme.dark]` in `.streamlit/config.toml` (auto-follows OS); Inter font; ChatGPT-green accent `#10A37F` | `base="auto"` is NOT a valid Streamlit 1.56 value — verified via WebFetch. Defining both subsections gives the same effect (Streamlit honors OS preference, user can override in app menu). |
| Sidebar shape | **Conversations-only**, ChatGPT-style. Time-bucket headers (`Today` / `Yesterday` / `Previous 7 days` / `Previous 30 days` / `Older`) via `st.caption`. Active conversation highlighted by passing `type="primary"` to the matching `st.button` (subtle accent tint via custom CSS, NOT the default saturated primary fill). | User explicitly picked this option over alternatives. |
| Settings location | `st.popover("⚙ Settings")` in the header — holds `top_k` slider + Stream toggle | Removes them from the cramped sidebar. |
| Upload location | `@st.dialog("Upload a document")` triggered by header button | Removes ~130 lines of visual noise from the sidebar. Existing upload+ingest poll loop preserved inside the dialog. |
| User identity | `st.popover` at the bottom of the sidebar; clicking reveals "Log out" | Natural document-flow placement (not flex-pinned to viewport bottom — Miguel picked moderate-CSS scope, not aggressive). |
| CSS approach | One injection block targeting only documented stable `data-testid` selectors (`stSidebar`, `stChatInput`, `stButton`, `stCaptionContainer`, `stVerticalBlock`); never target internal `.st-emotion-cache-*` classes | Stable across Streamlit minor upgrades. |
| Sources/confidence persistence | **Base64-encoded JSON in an AgentCore Memory `blob` payload item**, one per ASSISTANT turn | See §26.4 — Document-typed blobs round-trip poorly through `ListEvents`. |
| Migration | No backfill. Conversations created BEFORE this redeploy still reload as text-only (no blob sidecar). New turns persist sources/confidence going forward. | Acceptable for a demo; backfill would require a separate one-shot script. |

### 26.2 Files (this phase)

**Created**:
- [streamlit_client/.streamlit/config.toml](streamlit_client/.streamlit/config.toml) — theme + sidebar palette + Inter/JetBrains Mono fonts. Stays gitignored? No — `config.toml` is committed (it's UI config, not secrets); `secrets.toml` is the gitignored one.

**Heavy edits**:
- [streamlit_client/app.py](streamlit_client/app.py) — full rewrite of layout (header with title + Settings popover + Upload button; conversations-only sidebar with time-buckets and active highlight; user-identity popover at bottom). Replaced all 3 "Acme" references with "Knowledge Base". Added `_bucket_label()` + `_BUCKET_ORDER`. Promoted `top_k` and `use_stream` to `st.session_state` so the popover-controlled values survive reruns. Added `@st.dialog("Upload a document")`. Added `st.rerun()` after the first assistant message on a fresh session so the new conversation pops into the sidebar immediately (Issue 1 — was waiting for the next user action). Preserved all behavior: Cognito auth, JWT refresh, SSE streaming, INSUFFICIENT_CONTEXT marker strip, buffered `/query`, upload+ingest poll loop, conversation replay.
- [agent/rag.py](agent/rag.py) — `write_memory` now accepts `assistant_meta` and adds a third `{"blob": {"b64": <urlsafe-base64-of-JSON>}}` payload item. Caller in `run_query` passes `{sources, confidence, latency_ms, model_id, retrieval_strategy}`. Moved `latency_ms` calculation BEFORE `write_memory` so the value can be persisted.
- [lambda_stream/stream_app.py](lambda_stream/stream_app.py) — same change to `_write_memory`. Two call sites (the no-context path + the post-stream path) both pass the sidecar now.
- [lambda/conversations.py](lambda/conversations.py) — added `_extract_blob_meta()` helper that handles both the dict-form (write-side payload) AND the AgentCore-stringified-form (`{b64=<base64>}`) via regex. `get_conversation` now folds `{answer, sources, confidence, conversation_name, metadata}` into a `payload` field on the matching ASSISTANT message. Also normalizes role to lowercase at the boundary (fixed a small UX bug: historical messages were coming back as `"USER"/"ASSISTANT"` uppercase, which `st.chat_message` doesn't render default avatars for).

**Auto-touched** (CDK-side): [infra/cdk.json](infra/cdk.json) — CDK rewrote the `app` command from `env -u PYTHONPATH python3 -P app.py` → `env -u PYTHONPATH .venv/bin/python -P app.py`. Useful pin to the venv python; kept in the commit.

**Not changed**: PLAN.md (architecturally unchanged — just added Phase 13 row to phase table); README.md (added gotcha #8 + tiny note in §4.6; structure unchanged).

### 26.3 Live verification (2026-05-26 mid-day)

Two-pass deploy:
1. First deploy (sidecar as nested dict): smoke 7/7 PASS, but end-to-end test showed `has_payload=False` on reload — see §26.4 for the diagnosis.
2. Second deploy (base64 workaround): smoke 7/7 PASS, `has_payload=True` on reload for BOTH `/query` and `/query-stream`.

End-to-end evidence (verified by Miguel in browser):
- Fresh conversation → ask a question → response renders → **conversation entry appears in sidebar immediately** (Issue 1 fixed via `st.rerun()` on first-turn assistant message). ✓
- Click `+ New conversation` then click back to the previous conversation → **confidence badge + sources expander visible** under the assistant message (Issue 2 fixed via the blob sidecar). ✓
- Streaming path: same behavior on reload. ✓
- `scripts/smoke_test.py` → 7/7 PASS after final deploy. ✓
- 37 unit tests + 17 synth tests pass. ✓
- `cdk diff StorageStack AuthStack`: 0 differences (Phase 13 didn't touch them). ✓
- ApiStack URLs unchanged across both deploys — in-place container updates, not resource replacements.

### 26.4 The AgentCore Memory blob round-trip quirk (codified)

AgentCore Memory's `PayloadType` defines a `blob` member of type `Document` (Smithy doc:true, sensitive:true — verified via `botocore.loaders` against `service-2.json`). On **write**, you can pass any JSON-shape (dict, list, string, number) and AgentCore accepts it. But on **read** via `ListEvents`, the Document is returned to boto3 as a Python `str` rendering of the Java SDK's `toString()` output — looks like:

```
{retrieval_strategy=bedrock-kb-s3vectors-titan-v2-topk, model_id=anthropic.claude-haiku-4-5-20251001-v1:0, sources=[{snippet=..., score=0.63, ...}]}
```

Unquoted keys, `=` separator, no escaping. **Not parseable as JSON.**

Workaround applied: write the entire metadata payload as a urlsafe-base64-encoded JSON string inside a single Document field (`{"blob": {"b64": "<base64>"}}`). The toString output is then `{b64=<base64>}`, and base64's alphabet (`A-Za-z0-9_-=`) doesn't include `,` or `}`, so the value is regex-extractable cleanly. Decoder at [lambda/conversations.py:18-43](lambda/conversations.py#L18-L43).

Saved as [[feedback-agentcore-memory-blob-roundtrip]] for future sessions — this will bite any future code that tries to use AgentCore Memory blob payloads for structured data.

### 26.5 Things future-Claude should know about Phase 13

- The Streamlit client now reads `top_k` and `use_stream` from `st.session_state` (set in the Settings popover). The chat handler at the bottom reads from session_state too — don't move the popover code BELOW the handler or the values won't be defined on first run.
- The `_extract_blob_meta` helper has TWO code paths (dict + str) because boto3 SDK behavior for AgentCore blob reads might change in future SDK versions. If a future Claude session sees the dict path firing again (because boto3 fixed the round-trip), the regex path becomes dead code — safe to remove, but the dict path is the future-proof one.
- The `@st.dialog` decorator is stable as of Streamlit 1.35 (we're on 1.56). The upload flow now lives inside the dialog and uses `st.status` for the poll loop — this works inside dialogs, verified live.
- The CSS injection block uses `[data-testid="stCaptionContainer"]` for the time-bucket headers — this is a stable selector. The active-conversation highlight uses `button[kind="primary"]` (set via `type="primary"` on the matching button), with CSS overriding the default saturated primary fill to a subtle accent tint.
- `streamlit_client/.streamlit/config.toml` IS committed; `secrets.toml` IS NOT (gitignored). The two are different — config.toml is reproducible UI configuration; secrets.toml is account-specific runtime values.
- Migration: any pre-Phase-13 conversations don't have the blob sidecar. They reload text-only — the `_render_assistant` fallback path (`st.markdown(msg["content"])`) handles this correctly. If a future session wants full backfill, write a one-shot script that calls `list_events` for each (actor_id, session_id) and re-creates events with the blob. Not done in Phase 13 because (a) Miguel didn't ask, (b) the cost of re-running Bedrock retrieve+invoke for old turns is non-trivial.
- The deploy required Docker Desktop to be running. If Docker isn't up when `cdk deploy` runs, the agent container build fails with `failed to connect to the docker API`. `open -a Docker && sleep 30` reliably starts it.

### 26.6 Cost incurred this phase

~$0.20 total: two `cdk deploy AgentStack ApiStack` cycles (docker push + arm64 cross-build dominated each), ~10 Bedrock calls across smoke + end-to-end verification. No new resources. Cumulative project spend across 13 phases is still under **$3.50** of the $20 budget. AgentCore Runtime continues to idle at ~$0.20–0.50/day.

### 26.7 Memory updates this phase

- New feedback memory [[feedback-agentcore-memory-blob-roundtrip]] codifying the Document-type stringification quirk + base64 workaround.
- MEMORY.md index refreshed to include the new memory.

---

## 27. Phase 14 — CDK-native KB pre-seeding (2026-05-26)

Short session. One-file infra change so `cdk deploy --all` produces a chat-ready KB with no operator scripts. Brief §"minimum strong submission" lists "a pre-seeded or sample knowledge base using included sample documents"; until Phase 14 that was a two-script manual step (`scripts/upload_docs.py` + `scripts/start_ingestion.py`) tacked onto the deploy runbook. Now it's baked into the stack.

### 27.1 What changed

**[infra/stacks/storage_stack.py](infra/stacks/storage_stack.py)** — added two resources at the bottom of `StorageStack.__init__` (before the `CfnOutput` block):

1. **`aws_s3_deployment.BucketDeployment`** (`SeedDocsDeployment`) — syncs `sample-docs/*.md` into the docs bucket root (same key shape `scripts/upload_docs.py` uses, so eval source-match patterns like `document=refund-policy.md` stay valid). **`prune=False` is critical** — Phase 9 user uploads live under `uploads/{yyyy-mm-dd}/...` and would be wiped on every redeploy without it.
2. **Lambda-backed `CustomResource`** (`SeedKnowledgeBase` + `SeedIngestionFn`) — calls `bedrock-agent.StartIngestionJob` and polls every 5s up to a 240s deadline. Mirrors `scripts/start_ingestion.py` behavior (TERMINAL_OK={COMPLETE}, TERMINAL_FAIL={FAILED,STOPPED}). Same Lambda-backed CR pattern as `auth_stack.py` (Phase 11 lesson: dynamic-reference secrets don't resolve inside `Custom::AWS`). IAM scoped to `bedrock:StartIngestionJob`+`bedrock:GetIngestionJob` on the **KB ARN** (NOT data-source ARN — mirrors api_stack.py:258-266).

**Trigger semantics**: the CR has a `ContentHash` property = SHA-256 over `(filename, bytes)` pairs of seed docs, sorted. Unchanged corpus = unchanged hash = no CFN diff = no-op (no wasted Titan embedding spend). Changing any seed doc → new hash → CFN sends `Update` → CR re-fires → KB ingests the delta.

**[tests/test_synth.py](tests/test_synth.py)** — 3 new tests:
- `test_storage_stack_has_seed_bucket_deployment` (asserts `Custom::CDKBucketDeployment*` exists + `Prune=False`)
- `test_storage_stack_has_seed_ingestion_custom_resource` (asserts CR with `KbId+DataSourceId+ContentHash` properties)
- `test_seed_ingestion_lambda_has_bedrock_ingestion_actions` (asserts both bedrock ingestion verbs)

### 27.2 Live verification (2026-05-26 evening)

- 79/79 tests pass (59 unit + 20 synth, +3 new).
- `cdk diff StorageStack` was purely additive: 1 `Custom::CDKBucketDeployment` + 1 `AwsCliLayer` (BucketDeployment internals) + `SeedIngestionFn` + `SeedKnowledgeBase` CR + 1 LogRetention helper + 1 tag added to DocsBucket (`aws-cdk:cr-owned:<hash>`, BucketDeployment marker). No replacements. Other 3 stacks unchanged.
- `cdk deploy StorageStack` → `UPDATE_COMPLETE` in 145s.
- Ingestion job (description `cdk-seed`) → `COMPLETE`, `numberOfDocumentsScanned=9` (6 seed + 3 prior user uploads), `numberOfNewDocumentsIndexed=6` (all 6 seed docs ingested fresh), `numberOfDocumentsFailed=0`. ✓
- `scripts/smoke_test.py` → **7/7 PASS** against the freshly-seeded KB. `/query` returned 3 sources + confidence 0.680 from `refund-policy.md`. ✓

### 27.3 Things future-Claude should know about Phase 14

- **Single-stack `cdk deploy StorageStack` overwrites `cdk-outputs.json`** with only StorageStack's outputs — `scripts/smoke_test.py` then complains it can't find AuthStack/ApiStack outputs. Workaround: either deploy with `--all` (the no-diff stacks are no-ops), or regenerate the file from `aws cloudformation describe-stacks` for all 4 stacks. This is a long-standing CDK CLI behavior, unrelated to Phase 14.
- **Seed docs land at bucket root** (same as `scripts/upload_docs.py`), not under a `seed/` prefix. This preserves eval source-match patterns like `document=refund-policy.md`. If a future session wants to namespace seed vs. user content under different prefixes, update both the BucketDeployment destination prefix AND `tests/eval/questions.json` expected-source patterns.
- **The 4-minute Lambda timeout** is generous for 6 tiny markdown files (actual ingest takes ~15-20s including poll overhead). If a future seed corpus grows to dozens of MB or PDFs, raise the Lambda timeout (max 15min) and/or the `POLL_DEADLINE_S` constant inside the inline handler.
- **`scripts/upload_docs.py` + `scripts/start_ingestion.py` are not deleted** — they're still useful for one-off re-ingests without a full `cdk deploy`. README §10.2 documents this fallback.

### 27.4 Cost incurred this phase

~$0.05 total: one `cdk deploy StorageStack` (no docker push — StorageStack has no docker assets), one seed ingestion (6 small markdown files via Titan v2 ≈ $0.001), ~10 Bedrock invocations across smoke + verification. Cumulative project spend across 14 phases is still under **$3.55** of the $20 budget.

---

## 20. Closing pointer (post-project)

The core project is complete and **production-like deployed** (Phase 13 — live at https://rag-aws-kb.streamlit.app). If you start a new session in this repo:

1. **Read [README.md](README.md) first** — canonical reviewer entry point. §10.0 has the local two-command developer loop; §10.6 has the prod Streamlit Cloud runbook.
2. Read this handoff doc only if you need historical context: phase-by-phase build log, the two Phase-5 production incidents (§14), the password-sync incident (§24), the prod-deploy details (§25 = Phase 12), the UI redesign + sources-persistence work (§26 = Phase 13), or the most-recent **CDK-native KB pre-seeding work (§27 = Phase 14, most recent)**.
3. Read [PLAN.md](PLAN.md) only if you need the original implementation plan; the phase status table is current through Phase 14.
4. **Before assuming the live system is up**, run `aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE --region us-east-1` and `curl -I https://rag-aws-kb.streamlit.app/_stcore/health` — Miguel may have destroyed the stacks to zero cost. If stacks are gone, redeploy + re-paste Streamlit Cloud secrets ([§25.5 of this doc](#255-things-future-claude-should-know-about-phase-12) + [README §10.6](README.md)).
5. **If Miguel reports auth/login problems locally**, check first that he's launching via `./scripts/run_streamlit.sh` (it auto-syncs `secrets.toml` from live AWS). **If the problem is on the public URL**, it's almost always stale Streamlit Cloud secrets after a redeploy → re-run `python scripts/print_streamlit_cloud_secrets.py | pbcopy` and re-paste.
6. If asked to extend the project, do not re-litigate decisions in §3 / §21.1 / §22.1 / §25.1 — they are final. Propose new directions but treat the existing architecture as the load-bearing baseline.
7. **If Miguel asks about "optional extensions" or "what's next"**, point at [§19](#19-optional-extensions--candidate-list-for-future-sessions) and let him pick. Do not implement any of them proactively.

If asked: **"What's left?"** — the answer is "nothing *required*; **6 of 8 optional extensions are now done** (DDB + AgentCore Runtime + AgentCore Memory in Phase 8; streaming + upload/ingest in Phase 9; cost controls partially via Phase 12 APIGW throttling; plus Cognito JWT + Streamlit Cloud public deploy as bonuses). The remaining items in [§19](#19-optional-extensions--candidate-list-for-future-sessions) are CI/CD pipeline, guardrails/safety filters, human-feedback collection, and per-token usage tracking (the throttling-replaces-UsagePlan piece counts as partial cost-control, but a real budget alarm + per-`actor_id` rate limit are still open)."
