# Runbook — deploy, run, validate, tear down

Operator-facing instructions. The main [README](../README.md) summarizes; this file is the reference.

---

## 1. Prerequisites

- AWS CLI configured against an account with admin in `us-east-1`.
- Bedrock model access enabled in `us-east-1`:
  - Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`)
  - Claude Haiku 4.5 through the US cross-region inference profile
- Docker (for Lambda + agent image builds during `cdk deploy`).
- Python 3.12.
- Node.js (for the CDK CLI).
- `jq` (used by `scripts/run_streamlit.sh`).

---

## 2. Two-command day-to-day loop

After the one-time setup in [§3](#3-first-time-deploy-5-8-min-cold) is done once on a machine:

```bash
cd infra && env -u PYTHONPATH cdk deploy --all --require-approval never && cd ..
./scripts/run_streamlit.sh
```

The launcher reads live stack outputs from CloudFormation + Secrets Manager and rewrites `streamlit_client/.streamlit/secrets.toml` automatically. IDs that rotate on redeploy are picked up with no manual editing.

Demo username is `demo`; the password is in Secrets Manager (one-liner in [§5](#5-local-streamlit-client)).

---

## 3. First-time deploy (~5–8 min cold)

The first build cross-compiles the arm64 AgentCore image via QEMU on x86 Macs — slower the first time, then cached.

```bash
cd infra
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

env -u PYTHONPATH cdk bootstrap aws://<ACCOUNT_ID>/us-east-1

# Deploy all 4 stacks in dependency order
env -u PYTHONPATH cdk deploy --all --require-approval never \
  --outputs-file ../cdk-outputs.json
```

`env -u PYTHONPATH` is needed because the operator has `PYTHONPATH=/opt/spark/python:` set globally, which shadows the pip-installed `constructs` package.

**Test-user password sync** is automatic — the `AdminSetUserPassword` custom resource re-fires on every fresh deploy via its ARN-bound `physical_resource_id` (see Gotcha #4 in [ARCHITECTURE.md](ARCHITECTURE.md#7-gotchas-from-the-live-deploy)). If for any reason the first auth fails with `NotAuthorizedException`, the manual stop-gap is:

```bash
PWD_ARN=$(jq -r '.AuthStack.TestUserPasswordSecretArn' ../cdk-outputs.json)
UPID=$(jq -r '.AuthStack.UserPoolId' ../cdk-outputs.json)
aws cognito-idp admin-set-user-password --region us-east-1 \
  --user-pool-id "$UPID" --username demo --permanent \
  --password "$(aws secretsmanager get-secret-value --secret-id "$PWD_ARN" --region us-east-1 --query SecretString --output text)"
```

---

## 4. Seed corpus — automatic

StorageStack pre-seeds the KB with `sample-docs/*.md` during deployment (see [ARCHITECTURE.md §3.1](ARCHITECTURE.md#31-storagestack--infrastacksstorage_stackpy)). After `cdk deploy --all`, the KB is queryable immediately; no separate seeding step is required.

If you ever need to re-ingest manually (e.g. after editing a sample doc without redeploying), the legacy scripts still work:

```bash
source venv/bin/activate
python scripts/upload_docs.py       # MD5-idempotent upload of sample-docs/
python scripts/start_ingestion.py   # ~10s for 6 docs
```

---

## 5. Local Streamlit client

```bash
./scripts/run_streamlit.sh
# → http://localhost:8501
```

What it does ([scripts/run_streamlit.sh](../scripts/run_streamlit.sh)):

- `aws cloudformation describe-stacks` on `AuthStack` + `ApiStack` → reads live `UserPoolId`, `UserPoolClientId`, `UserPoolClientSecretArn`, `ApiUrl`, `StreamFunctionUrl`.
- `aws secretsmanager get-secret-value` → App Client secret.
- Rewrites `streamlit_client/.streamlit/secrets.toml` with every required `[auth]` / `[api]` / `[stream]` key populated. Preserves an existing `cookie_secret` so live sessions survive.
- Launches `./venv/bin/python -m streamlit run streamlit_client/app.py`.

Idempotent and safe to run every session. Fails fast if any required output is missing.

To fetch the demo user's password manually (e.g. for the Hosted UI):

```bash
PWD_ARN=$(aws cloudformation describe-stacks --region us-east-1 --stack-name AuthStack \
  --query "Stacks[0].Outputs[?OutputKey=='TestUserPasswordSecretArn'].OutputValue | [0]" --output text)
aws secretsmanager get-secret-value --region us-east-1 --secret-id "$PWD_ARN" \
  --query SecretString --output text
```

**Port pinning**: the Cognito App Client is provisioned with `http://localhost:8501/oauth2callback` as the only local callback. Streamlit must bind to port 8501.

---

## 6. Browser walkthrough

1. Open `http://localhost:8501`. Streamlit shows "Sign in with your Cognito account" + a **Log in with Cognito** button.
2. Click it. The browser redirects to the Cognito Hosted UI. Sign in as `demo` with the password from Secrets Manager (the same one the smoke test uses).
3. Cognito redirects back to `http://localhost:8501/oauth2callback`; Streamlit reads the ID token and persists it in `st.user.tokens["id"]`.
4. **Chat**: type a question in the bottom chat input. With "Stream responses" ON (default), the answer streams in token-by-token — first token typically in 1–2s after the container warms (the first call may take 4–6s for cold start; subsequent calls are 1–1.5s).
5. The Sources expander appears below each answer with up to `top_k` chunks (document, S3 URI, score, snippet).
6. **Sidebar — past conversations**: click a conversation name to load that session's transcript. Names are LLM-generated on the first turn of each session.
7. **Sidebar — upload**: pick a file (≤50 MB, one of the 10 supported MIME types) and click "Ingest into knowledge base". A status panel streams the four steps (mint URL → S3 PUT → start ingestion → poll), reaching "Ingestion complete" in roughly 6–15s for a small document. The new doc is immediately queryable.
8. **Sign out**: the sidebar **Log out** button clears the cookie + redirects to Cognito's logout URL.

---

## 7. Validate

```bash
python scripts/smoke_test.py        # 7 checks against the live API
python tests/eval/run_eval.py       # 8 questions, ~20s, writes tests/eval/eval_results.md
```

The smoke test exits 0 on full pass and writes a tabular report to stdout. The eval harness writes [tests/eval/eval_results.md](../tests/eval/eval_results.md) with per-question results + aggregate metrics.

---

## 8. Tear down (zero ongoing cost)

```bash
cd infra && source .venv/bin/activate
env -u PYTHONPATH cdk destroy --all --force
```

`RemovalPolicy.DESTROY` is set on all stateful resources for clean tear-down. AgentCore Runtime container costs ~$0.20–0.50/day idle if left up; destroy between active sessions.

Lambda's auto-created ECR repos may linger — `aws ecr delete-repository --force` if you want full zero.

---

## 9. Production-like deployment (Streamlit Community Cloud)

The same 4 CDK stacks back a publicly reachable Streamlit app on Streamlit Community Cloud. No additional AWS infra is provisioned for the client (AWS App Runner stopped accepting new customers as of 2026-04-30; Streamlit Cloud is the zero-overhead path for a Streamlit-shaped app).

The CDK is already prod-shaped:

- Cognito App Client registers both `http://localhost:8501/oauth2callback` and the Streamlit Cloud callback URL.
- APIGW preflight + Function URL CORS allow both origins.
- Method-level throttling caps abuse on every authenticated route.

**One-time setup** (after `cdk deploy --all` and a public GitHub push):

1. Connect the repo to Streamlit Cloud at https://share.streamlit.io. Set:
   - Main file path: `streamlit_client/app.py`
   - Python version: 3.12
   - (Cloud auto-detects `streamlit_client/requirements.txt`.)
2. Reserve the subdomain in **App settings → General → URL**. It must match the Cognito callback URL registered in [auth_stack.py](../infra/stacks/auth_stack.py).
3. Paste secrets — locally run the helper and paste into **App settings → Secrets**:
   ```bash
   python scripts/print_streamlit_cloud_secrets.py | pbcopy
   ```
   The script reads live CFN + Secrets Manager and writes a TOML block with `redirect_uri`, Cognito + API + stream URLs. The `cookie_secret` is persisted locally in `.streamlit_cloud_cookie_secret` (gitignored) so subsequent re-pastes keep live sessions warm.
4. Streamlit Cloud auto-restarts on secret save and on every GitHub push to the connected branch.

**Recurring routine after `cdk destroy --all` + redeploy**: re-run step 3 only. CFN re-deploys take ~7 min; re-pasting secrets is ~10s.

The browser flow is identical to [§6](#6-browser-walkthrough) but Cognito redirects back to the Streamlit Cloud URL instead of localhost.

---

## 10. Cost summary

| State | Cost |
|---|---|
| Idle (stacks up, no traffic) | ≈ $0.40/month flat (Secrets Manager + ECR) + AgentCore Runtime ~$0.20–0.50/day |
| Per query | ≈ $0.0003 (Haiku tokens dominate) |
| Per upload + ingestion | ≈ $0.001 (Titan embeddings) |

Cumulative project spend across all phases stayed under $3 of the $20 budget.
