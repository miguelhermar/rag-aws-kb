# Operator scripts

Operator tooling for the current Cognito-authenticated RAG-AWS deployment.
Most scripts read defaults from `../cdk-outputs.json` or live CloudFormation
outputs and accept CLI overrides where useful.

Run Python scripts with the repo venv when available:

```bash
/Users/miguelhermar/Desktop/RAG-AWS/venv/bin/python scripts/<name>.py [args]
```

## run_streamlit.sh

Syncs `streamlit_client/.streamlit/secrets.toml` from live CloudFormation
outputs and Secrets Manager, then starts the local Streamlit app.

It configures:

- Cognito OAuth discovery URL
- App Client ID and secret
- local redirect URI
- REST API base URL
- streaming Function URL

```bash
./scripts/run_streamlit.sh
```

Prereqs: `AuthStack` and `ApiStack` deployed, AWS credentials for
CloudFormation and Secrets Manager, plus `jq`.

## print_streamlit_cloud_secrets.py

Prints the complete TOML block for Streamlit Community Cloud secrets. Use this
after a fresh deploy or after `cdk destroy --all` plus redeploy, because
Cognito IDs and generated secrets change.

```bash
python scripts/print_streamlit_cloud_secrets.py
python scripts/print_streamlit_cloud_secrets.py --redirect-uri https://example.streamlit.app/oauth2callback
```

Typical macOS copy flow:

```bash
python scripts/print_streamlit_cloud_secrets.py | pbcopy
```

Prereqs: `AuthStack` and `ApiStack` deployed; AWS credentials for
CloudFormation and Secrets Manager.

## get_id_token.py

Fetches a Cognito ID token for the configured demo user. This is useful for
manual curl calls and is also used by the smoke test.

```bash
TOKEN=$(python scripts/get_id_token.py)
```

By default it reads Cognito and secret ARNs from `cdk-outputs.json`.

Prereqs: `AuthStack` deployed; AWS credentials for Cognito IDP and Secrets
Manager.

## smoke_test.py

Runs the live end-to-end smoke test for the deployed API.

Checks include:

- unauthenticated `/health`
- authenticated buffered `/query`
- missing-token rejection
- validation error handling
- conversation listing
- streaming `/query-stream` over SSE
- upload, ingestion, and retrieval of a temporary document

```bash
python scripts/smoke_test.py
```

By default it reads API and Cognito values from `cdk-outputs.json` and obtains
a Cognito ID token through `get_id_token.py`.

Prereqs: all four stacks deployed, seed ingestion complete, AWS credentials for
Secrets Manager and Cognito IDP, and outbound HTTPS access to the deployed API.

## upload_docs.py

Walks `sample-docs/*.md` and uploads each file to the S3 docs bucket.
Idempotent: skips files whose S3 ETag already matches the local MD5
(S3 ETag = MD5 for single-part uploads, which all our few-KB files are).

```bash
python scripts/upload_docs.py
python scripts/upload_docs.py --bucket my-bucket --source-dir other/ --region us-east-1
```

Output:
```
[uploaded] refund-policy.md (2741 bytes)
[skipped]  shipping-policy.md (2103 bytes)
...
3 uploaded, 3 skipped, 6 total
```

This is now a manual fallback. Fresh deploys upload seed docs through
`StorageStack` using `BucketDeployment`.

Prereqs: `StorageStack` deployed; AWS credentials with `s3:PutObject` and
`s3:HeadObject`.

## start_ingestion.py

Calls `bedrock-agent.start_ingestion_job` and polls `get_ingestion_job` every 10s
(5-min timeout) until status reaches a terminal state. On `COMPLETE`, prints
the ingestion statistics (scanned / indexed / failed). On `FAILED` or `STOPPED`,
prints `failureReasons` and exits 1.

```bash
python scripts/start_ingestion.py
python scripts/start_ingestion.py --kb-id KBID --data-source-id DSID --timeout 600
```

Output:
```
Starting ingestion job: kb=W6ZGK8YJWU ds=91I2LVEI5L
Started job: <uuid>
[0s] status=STARTING
[10s] status=IN_PROGRESS
[40s] status=COMPLETE

Ingestion COMPLETE
  numberOfDocumentsScanned:     6
  numberOfNewDocumentsIndexed:  6
  ...
```

This is now a manual fallback. Fresh deploys trigger seed ingestion through the
`SeedKnowledgeBase` custom resource in `StorageStack`.

Prereqs: docs already uploaded to the docs bucket, KB and DataSource in
`ACTIVE` / `AVAILABLE` state, and AWS credentials with
`bedrock:StartIngestionJob` and `bedrock:GetIngestionJob` on the KB.

## Typical workflow (clean account -> end-to-end working RAG)

```bash
# 0. Bootstrap + deploy all stacks
cd infra
cdk bootstrap aws://<acct>/us-east-1
cdk deploy --all --outputs-file ../cdk-outputs.json

# 1. Run local Streamlit against the deployed stacks
cd ..
./scripts/run_streamlit.sh

# 2. Smoke-test the live API
python scripts/smoke_test.py

# 3. Optional manual fallback if you need to re-upload/re-ingest sample docs
python scripts/upload_docs.py
python scripts/start_ingestion.py
```
