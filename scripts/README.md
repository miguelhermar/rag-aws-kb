# Operator scripts

Three one-shots to populate the Bedrock Knowledge Base and rotate the API key.
All three read defaults from `../cdk-outputs.json` (produced by `cdk deploy --outputs-file ...`)
and accept CLI overrides for every value. Stdlib + boto3 only.

Run with the legacy venv's python (boto3 already installed):

```bash
/Users/miguelhermar/Desktop/RAG-AWS/venv/bin/python scripts/<name>.py [args]
```

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

Prereqs: `cdk deploy StorageStack` complete; AWS creds with `s3:PutObject` + `s3:HeadObject`.

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

Prereqs: docs already uploaded to the docs bucket (run `upload_docs.py` first);
KB and DataSource in `ACTIVE` / `AVAILABLE` state; AWS creds with
`bedrock:StartIngestionJob` + `bedrock:GetIngestionJob` on the KB.

## rotate_api_key.py

Rotates the API key value stored in Secrets Manager. **Does NOT print the new
value** — fetch it from Secrets Manager afterwards.

```bash
python scripts/rotate_api_key.py
python scripts/rotate_api_key.py --secret-arn arn:aws:secretsmanager:...
```

Output:
```
Rotated. New value stored in Secrets Manager.

To propagate to API Gateway:
  1. cd infra
  2. Trigger a resource update on the ApiKey so CFN re-resolves the dynamic reference.
     ...
```

### Rotation flow — why it's two-step and not one

The CDK `ApiKey` is defined as:
```python
api_key = apigw.ApiKey(self, "RagApiKey",
    value=storage_stack.api_key_secret.secret_value_from_json("apiKey").unsafe_unwrap())
```

`unsafe_unwrap()` emits a CloudFormation dynamic reference of the form
`{{resolve:secretsmanager:<arn>:SecretString:apiKey}}`. Per the
[CFN dynamic references docs](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/dynamic-references-secretsmanager.html),
this is resolved exactly once when the resource is created/updated. **Rotating the
secret value in Secrets Manager does not push the new value into API Gateway.**

Per the [API Gateway patch-operations docs](https://docs.aws.amazon.com/apigateway/latest/api/patch-operations.html),
`UpdateApiKey` does **not** support patching `/value` (supported paths are
`/customerId`, `/description`, `/enabled`, `/labels`, `/name`, `/stages` only).
So there is no SDK call that can change an existing API key's value in-place.

Deleting and recreating the API key out-of-band would cause CDK drift — the next
`cdk deploy` would see the resource missing and try to recreate (or fail).

**Therefore the correct flow is**:
1. `rotate_api_key.py` updates the Secrets Manager value (source of truth).
2. Operator runs `cdk deploy ApiStack` with a forced resource update on the ApiKey.
   A pure no-op deploy will not re-resolve the dynamic reference; CFN only re-reads
   it when the resource itself is updated.

Concrete recipe to force the resource update:
```bash
cd infra
# Edit infra/stacks/api_stack.py: add description="rotated-YYYY-MM-DD" to apigw.ApiKey(...)
cdk deploy ApiStack --outputs-file ../cdk-outputs.json
# Verify the rotated key works:
NEW_KEY=$(aws secretsmanager get-secret-value --secret-id <ARN> \
  --region us-east-1 --query SecretString --output text | python -c 'import sys,json;print(json.load(sys.stdin)["apiKey"])')
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<api>/prod/query \
  -H "x-api-key: $NEW_KEY" -H "Content-Type: application/json" \
  -d '{"question":"test","top_k":1}'
# Should print 200. Then revert the description bump on the next normal deploy.
```

Prereqs: AWS creds with `secretsmanager:PutSecretValue` on the secret ARN, and
CDK creds for the `cdk deploy ApiStack` step.

## Typical workflow (clean account → end-to-end working RAG)

```bash
# 0. Bootstrap + initial deploy (Phase 2 + 4)
cd infra
cdk bootstrap aws://<acct>/us-east-1
cdk deploy StorageStack ApiStack --outputs-file ../cdk-outputs.json

# 1. Populate the docs bucket and trigger ingestion (Phase 5)
cd ..
python scripts/upload_docs.py
python scripts/start_ingestion.py

# 2. Smoke-test the API end-to-end (Phase 4 contract, now with real docs)
API_URL=$(jq -r '.ApiStack.ApiUrl' cdk-outputs.json)
SECRET_ARN=$(jq -r '.StorageStack.ApiKeySecretArn' cdk-outputs.json)
API_KEY=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" \
  --region us-east-1 --query SecretString --output text \
  | python -c 'import sys,json;print(json.load(sys.stdin)["apiKey"])')
curl -X POST "${API_URL}query" \
  -H "x-api-key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"question":"What is the refund policy?","top_k":3}' | jq .

# 3. (Optional) Rotate the API key
python scripts/rotate_api_key.py
# then follow the recipe in "Rotation flow" above
```
