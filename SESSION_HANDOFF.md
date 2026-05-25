# Session Handoff — RAG-AWS Productionization

**Purpose**: If you're a fresh Claude session opening this file, read it end-to-end. It contains everything you need to continue this project without losing context. The user expects you to resume from "Next Phase" without re-asking questions that are already settled below.

**Last updated**: 2026-05-24 (evening), after completing Phase 6 (Streamlit client + smoke test + eval harness, all verified live against fresh redeploy). Phases 1-3 in `e3925e5`, Phase 4 in `e7c6d14`, Phase 5 in `20add17`, handoff cleanup in `602822d`, Phase 6 in the bundled commit referenced as the most recent on `main`.

**Important**: between the last session and this one, Miguel ran `cdk destroy ApiStack StorageStack` to zero out idle AWS cost. Phase 6 began with a full redeploy of both stacks. All resource IDs in §8 are FRESH from the 2026-05-24-evening redeploy — the IDs in earlier session-handoff revisions are stale and should be ignored.

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
| 7 | Final README + cleanup + hardening notes | ⏭️ NEXT (docs-only) |

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

**Account**: `954863244564`, IAM user `admin_user`, region `us-east-1`.

**StorageStack — deployed, `CREATE_COMPLETE`, 14 resources, ~62s wall-clock.**

**ApiStack — deployed, 22 resources `CREATE_COMPLETE`, ~70s wall-clock (docker layer cache warm).**

Outputs written to [cdk-outputs.json](cdk-outputs.json) (gitignored). DO NOT memorize specific IDs — they rotate on every redeploy. Read from `cdk-outputs.json` via `jq` and from Secrets Manager for the API key. The current values as of this writing:
```
# StorageStack
KbId            = LA8DA5P7HH
KbArn           = arn:aws:bedrock:us-east-1:954863244564:knowledge-base/LA8DA5P7HH
DataSourceId    = UNMM05LJHV
DocsBucketName  = storagestack-docsbucketecea003f-u6kkxkfltmgj
DocsBucketArn   = arn:aws:s3:::storagestack-docsbucketecea003f-u6kkxkfltmgj
VectorBucketArn = arn:aws:s3vectors:us-east-1:954863244564:bucket/rag-aws-vectors-244564
VectorIndexArn  = arn:aws:s3vectors:us-east-1:954863244564:bucket/rag-aws-vectors-244564/index/rag-aws-kb-index
ApiKeySecretArn = arn:aws:secretsmanager:us-east-1:954863244564:secret:ApiKeySecretF1B08E61-0nj3ocgfH4cZ-iifl5w

# ApiStack
ApiUrl              = https://id04zftvx5.execute-api.us-east-1.amazonaws.com/prod/
LambdaFunctionName  = ApiStack-RagHandler014AF978-2DxedjA9s4yl
LogGroupName        = /aws/lambda/ApiStack-RagHandler
```

**Live status verified via CLI + curl + scripts/smoke_test.py + tests/eval/run_eval.py (2026-05-24 evening, end of Phase 6)**:
- KB `LA8DA5P7HH` → status `ACTIVE`, storage `S3_VECTORS`, embed Titan v2 ✅
- DataSource `UNMM05LJHV` → status `AVAILABLE`, chunking `FIXED_SIZE`; **6/6 sample-docs ingested** ✅
- Docs bucket → 6 `.md` files uploaded via `scripts/upload_docs.py` (idempotent) ✅
- Vector index `rag-aws-kb-index` → dim=1024, metric=cosine, dtype=float32 ✅
- API key secret → exists; value `{"apiKey": "<32 alnum chars>"}` (never printed) ✅
- **`scripts/smoke_test.py` → 4/4 PASS**: `/health` 200, `/query` valid+key 200 schema-valid + sources non-empty, `/query` no-key 403, `/query` empty-question 400 `InvalidRequest` ✅
- **`tests/eval/run_eval.py` → committed [tests/eval/eval_results.md](tests/eval/eval_results.md)**: source-match 100% (6/6 in-corpus), mean in-corpus confidence 0.813, off-corpus confidence clamped to 0.200, total wall-clock 20.2s for 8 questions ✅
- **`cdk diff` on both stacks**: 0 differences (zero IaC drift — Phase 6 touched only client/test/eval code) ✅

**User pre-flight done before deploy**:
- Bedrock model access for Titan v2 (`amazon.titan-embed-text-v2:0`) is `ACTIVE` in us-east-1.
- Haiku 4.5 inference profile `us.anthropic.claude-haiku-4-5-20251001-v1:0` is `ACTIVE` and does not require Marketplace subscription.
- `cdk bootstrap aws://954863244564/us-east-1` (CDKToolkit stack exists, unchanged across redeploys).

**Cost incurred so far this session (Phase 6)**: ~$0.10 (full redeploy: docker push to ECR + ingestion + smoke runs + 8-question eval). Idle cost going forward ≈$0.40/month (Secrets Manager flat fee + ECR storage). Active cost: ~$0.0003 per `/query` (Haiku tokens dominate; APIGW + Lambda compute are rounding error). Cumulative project total still well under $1 of the $20 budget.

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
- `grep` for hardcoded `QWM9CGVIJL`/account/secrets across `lambda/` + `tests/` → **0 matches**, all env-driven

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

**Live verification (2026-05-24)** — three curl smoke tests against `https://i93jgleje5.execute-api.us-east-1.amazonaws.com/prod/`:
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

## 16. Next phase brief (Phase 7 — Final README + cleanup + hardening notes)

**Goal**: Reviewer can stand up + tear down the entire system in <30 minutes using only the README. The README satisfies brief [§14.3](AWS%20Native%20Knowledge%20Base%20Agent%20Candidate%20Project%20Brief.md) ("Documentation Requirements").

**Files**:
- [README.md](README.md) — full rewrite (currently a stub from Phase 1).
- *Possibly* an `architecture.png` or `architecture.svg` (a simple service-flow diagram). If we don't want to bother with image generation, an ASCII diagram in the README is acceptable.
- No code changes intended. If we choose to clean up the `RAG-demo/`-era shebangs in `venv/`, that's a one-line `rm -rf venv && python3 -m venv venv && venv/bin/pip install -r tests/requirements.txt` — but it's optional and worth a brief conversation first.

**README sections required by brief §14.3 + our internal lessons**:
1. **Overview** — one-paragraph what this is + the live status (resource IDs come from `cdk-outputs.json`, not hardcoded).
2. **Architecture diagram** — services + data flow. AWS-only path: Client (Streamlit) → APIGW (API key) → Lambda (container) → Bedrock Retrieve (KB on S3 Vectors) → Bedrock InvokeModel (Haiku 4.5 inference profile) → response with grounded citations + confidence.
3. **Services used** — APIGW REST, Lambda container, S3 (docs), S3 Vectors (index), Bedrock KB, Bedrock Runtime, Titan v2 embeddings, Secrets Manager, CloudWatch Logs, IAM. One line per service.
4. **API contract** — request/response/error schemas (copy from [lambda/schemas.py](lambda/schemas.py) and [PLAN.md §"Schemas"](PLAN.md)) + auth header (`x-api-key`).
5. **RAG behavior** — chunking (FIXED_SIZE 300/20%), retrieval (top-k cosine over Titan v2), prompt (grounded with `INSUFFICIENT_CONTEXT` literal), confidence formula, INSUFFICIENT_ANSWER override.
6. **Sample docs** — list the 6 fictional Acme Notes docs + the seed prompt that generated them.
7. **How to deploy** — `cdk bootstrap` → `cdk deploy StorageStack ApiStack` → `python scripts/upload_docs.py` → `python scripts/start_ingestion.py` → copy `streamlit_client/.streamlit/secrets.toml.example` → `streamlit run streamlit_client/app.py`. Each step with one-line "why" and an indicative wall-clock.
8. **How to verify** — `python scripts/smoke_test.py` (must exit 0) + `python tests/eval/run_eval.py` (writes a fresh `eval_results.md`).
9. **How to tear down** — `cdk destroy ApiStack StorageStack --force`. Note that `autoDeleteObjects=True` empties the docs bucket and that `AwsCustomResource.onDelete` handles the vector index. Mention that ECR image (Lambda asset) stays until the asset cleanup lambda runs — manual `aws ecr delete-repository` may be required for full cost zero.
10. **Changes from sample / what we replaced** — link `legacy/` and explain that FAISS → S3 Vectors, Gemini → Bedrock Claude Haiku 4.5, in-process loaders → KB ingestion, in-process retrieval → KB Retrieve, no FastAPI (handler is Lambda native).
11. **AI tools used in build** — Claude Code orchestration + sub-agents, with the phased approach + trust-but-verify gate at each phase.
12. **Assumptions made** — `us-east-1`-only, account `954863244564`, model access pre-enabled, ~$1 active spend cap, manual ingestion (no event-driven), API-key auth (not Cognito).
13. **Data flow security note** — explain explicitly that requests/responses never leave AWS (no third-party APIs in the runtime path), the API key is the only outbound secret (delivered to the local Streamlit client via `secrets.toml`), and CloudWatch Logs are the only place a question is persisted server-side.
14. **Hardening notes — what we'd do for production** — copy [PLAN.md §"Production hardening"](PLAN.md): VPC + interface endpoints, WAF, KMS CMKs, X-Ray tracing, CloudWatch budget alarms at $10/$18, per-identity rate limiting via Cognito JWT, output PII redaction, Bedrock model fallback (Haiku → Sonnet), async ingestion pipeline (S3 → EventBridge → Step Function), SnapStart when GA for container Lambdas. Add the Phase 5 lesson (model deprecation guardrails) and the Phase 6 lesson (prompt-tune for ambiguous questions if multi-doc synthesis is desired).
15. **Leaked-secret note** — the legacy `.env` at repo root still contains a Gemini key from the original prototype (gitignored, never pushed, but lives on Miguel's disk). New stack does not use Gemini; the key should be rotated/deleted by Miguel.

**Verification before declaring Phase 7 done**:
1. A fresh reader of just the README can run through the 6 steps and end up with a working system. Test this by following our own README from a clean shell.
2. All file references in the README resolve (no broken markdown links).
3. `git status` clean; nothing uncommitted.
4. Final `git log` shows a sensible phase-based commit history.

**Cost note**: docs-only phase = $0 active cost. (No new AWS deploys planned; if `cdk destroy` is run after the README is verified, idle cost drops to $0 once the ECR repo is removed.)

**How to execute**: probably inline by the orchestrator rather than a sub-agent — it's a docs-only phase and the synthesis benefits from full session context. If Miguel prefers a sub-agent (e.g., to draft a first cut while the orchestrator does something else), use that pattern; the existing handoff doc gives the sub-agent enough context to write a strong README.

---

## 17. Things to remember about this user

- They asked to "leverage the compact skill" but there is no literal `/compact` skill — interpret as "write a dense session handoff".
- They prefer terse, opinionated responses over menus.
- They have given standing authorization to deploy to their AWS account `954863244564/us-east-1`. Always confirm before destructive operations (`cdk destroy`, `aws s3 rb`).
- They appreciate cost transparency — quote idle/active costs when proposing AWS actions.
- They're using VS Code; file references should be markdown links `[name.ext](relative/path)`.
- They sometimes `cdk destroy` between sessions to zero out idle cost — at session start, always run `aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE --region us-east-1` (or just `jq` on `cdk-outputs.json` + a `describe-stacks` call) to confirm whether StorageStack/ApiStack are still up before assuming any live IDs are valid.

---

## 18. Closing pointer

If you start a new session: read this file end-to-end, then [PLAN.md](PLAN.md). Then check whether the stacks are deployed (see §17 last bullet). Then ask Miguel: **"Ready to start Phase 7 (final README + cleanup + hardening notes)? The system is fully deployed and verified end-to-end — Phase 7 is the docs-only wrap that makes the project reviewable in <30 minutes."** Do not re-derive decisions from scratch; the decisions in §3 are final unless Miguel explicitly reopens them.
