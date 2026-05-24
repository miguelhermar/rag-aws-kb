# RAG-AWS — Knowledge Base Agent on AWS

> **Status: Under construction.** This README is a stub. The full reviewer-facing README is delivered in Phase 7 of [PLAN.md](PLAN.md).

An AWS-native, CDK-deployed, authenticated RAG service over a sample enterprise knowledge base. A local Streamlit client calls an Amazon API Gateway endpoint that fronts an AWS Lambda agent, which retrieves grounded chunks from an Amazon Bedrock Knowledge Base (S3 Vectors backend) and generates answers with Anthropic Claude 3 Haiku.

## Repository layout

| Path | Purpose |
|---|---|
| [PLAN.md](PLAN.md) | Implementation plan (architecture, phases, decisions, risks). |
| [sample-docs/](sample-docs/) | 6 sample Markdown documents that seed the knowledge base. |
| `infra/` | AWS CDK (Python) app — created in Phase 2 / Phase 4. |
| `lambda/` | Container-based Lambda handler — created in Phase 3. |
| `streamlit_client/` | Local Streamlit client — created in Phase 6. |
| `scripts/` | Operator scripts (upload docs, start ingestion, smoke test, rotate key). |
| `tests/` | Schema tests + evaluation harness. |
| [legacy/](legacy/) | The original Gemini + FAISS + Streamlit prototype, preserved for reference and diff. See [legacy/README.md](legacy/README.md). |

## Quick links

- Project brief: [AWS Native Knowledge Base Agent Candidate Project Brief.md](AWS%20Native%20Knowledge%20Base%20Agent%20Candidate%20Project%20Brief.md)
- Implementation plan: [PLAN.md](PLAN.md)
- Original prototype docs: [legacy/README-original.md](legacy/README-original.md)
