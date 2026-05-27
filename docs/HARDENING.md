# Production hardening — what's in vs. what's deferred

The submission is interview-grade with several production-shaped pieces in place. Items in [§1](#1-implemented) are live in the CDK; items in [§2](#2-deferred--production-roadmap) are deliberate cuts to keep the demo small + cheap, and would be the next moves toward a real production posture.

---

## 1. Implemented

- **Per-user identity** via Cognito JWT; per-user data isolation enforced by `actor_id`-keyed DynamoDB + AgentCore Memory.
- **Least-privilege IAM** on all Lambdas + the AgentCore Runtime — specific ARNs, no wildcards. `s3:PutObject` scoped to `uploads/*` only; `bedrock:Invoke*` scoped to the Haiku 4.5 inference profile + the 3 foundation-model fan-out regions.
- **Structured JSON logs** with per-request `request_id` correlation across both Lambdas + APIGW + AgentCore Runtime.
- **Schema validation** (pydantic v2) at every request boundary; never leak exception detail (full traceback in CloudWatch only).
- **30-day log retention**; AgentCore Memory `EventExpiryDuration=30 days`.
- **Secrets via Secrets Manager** — no secret values in synth output. Test-user password sync via a Lambda-backed Custom Resource that reads from Secrets Manager at runtime (Phase 11 hardening — see [ARCHITECTURE.md Gotcha #4](ARCHITECTURE.md#7-gotchas-from-the-live-deploy)).
- **`autoDeleteObjects=True`** on the docs bucket + `RemovalPolicy.DESTROY` on most resources for clean tear-down.
- **Streaming token-level UX** + **runtime upload + ingestion** with progress feedback.
- **APIGW method-level throttling** — per-method rate/burst caps on every authenticated route (`/query` 10/20, `/ingest` 5/10, others 10/20) + stage backstop 20/40. Replaces a static API key + UsagePlan without re-introducing API keys.
- **In-handler JWT validation** on the streaming Function URL (the Function URL itself is `AuthType=NONE` because Streamlit can't SigV4-sign).
- **CDK-native KB pre-seeding** via `BucketDeployment` + Lambda-backed CR. SHA-256 of the seed corpus makes unchanged redeploys no-ops; `prune=False` preserves user uploads.

---

## 2. Deferred — production roadmap

These are deliberate cuts. None of them are required by the take-home brief; each adds operational weight beyond what a demo benefits from.

- **Per-user (`actor_id`) rate limits** + **APIGW WAF** (rate, geo, OWASP common rules). Current throttling is account-wide per-method, not per-caller.
- **VPC + interface endpoints** for Bedrock, Secrets Manager, S3, DynamoDB; both Lambdas in private subnets.
- **KMS CMKs** on the docs bucket, all secrets, all log groups, the DDB table, and AgentCore Memory.
- **X-Ray tracing** through APIGW → Lambda → AgentCore Runtime → Bedrock; budget alarms; per-user dashboards.
- **Bedrock model fallback** (Haiku → Sonnet with backoff); SnapStart for container Lambdas when GA; DLQ for failed invocations.
- **Event-driven background ingestion** (S3 `PutObject` → EventBridge → `StartIngestionJob`) as a complement to the explicit `/ingest` route — for bulk uploads where polling per file is too chatty.
- **AgentCore Gateway** for sharing tools across multiple agents; **AgentCore Identity** for per-tool fine-grained auth.
- **PII handling** — output redaction before logging; input PII scan + reject on `/query`.
- **RAG quality** — query-rewriting for ambiguous questions; reranking (Bedrock supports it); per-source recall + groundedness eval gate in CI.
- **Per-user upload isolation** — uploads currently land in a shared `uploads/` prefix; a real product would namespace by `actor_id` and filter KB retrieval accordingly.
- **CI/CD pipeline** for CDK + multi-stage separation (dev/staging/prod), multi-account.
- **Guardrails / safety filters** via Bedrock Guardrails on both retrieval and generation.
- **Human feedback collection** — thumbs up/down per turn persisted alongside the AgentCore Memory event for later RLHF / eval.
- **Cost controls + token tracking** — per-`actor_id` token budgets, CloudWatch metrics on input/output tokens per turn, billing alarm.
- **Consolidate the `agent/rag.py` ↔ `lambda_stream/stream_app.py` duplication.** The system prompt, INSUFFICIENT_CONTEXT marker handling, confidence formula, and Memory + DDB write logic exist in both files because Docker contexts are isolated and `InvokeAgentRuntime` is buffered-only as of May 2026. A CI lint that fails on divergence would catch drift. If/when AgentCore Runtime exposes a streaming counterpart, both paths can collapse into the runtime.
