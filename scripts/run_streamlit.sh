#!/usr/bin/env bash
# Sync streamlit_client/.streamlit/secrets.toml from the LIVE CloudFormation
# stacks + Secrets Manager, then launch the Streamlit app.
#
# Routine after `cdk deploy --all` (no `--outputs-file` flag needed):
#   ./scripts/run_streamlit.sh
#
# Idempotent — safe to run every session. Outputs are read directly from
# CFN describe-stacks so the secrets file can never drift from live state.
# Preserves an existing cookie_secret so live Streamlit session cookies survive.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SECRETS_DIR="streamlit_client/.streamlit"
SECRETS_FILE="$SECRETS_DIR/secrets.toml"
REGION="${AWS_REGION:-us-east-1}"

for cmd in jq aws; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: '$cmd' not found in PATH." >&2
    exit 1
  fi
done

# Pulls one output value from a deployed stack. Exits with a clear error if
# the stack or key is missing (= "you forgot to cdk deploy").
get_output() {
  local stack="$1" key="$2" val
  val=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$stack" \
        --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue | [0]" \
        --output text 2>/dev/null || true)
  if [[ -z "$val" || "$val" == "None" ]]; then
    echo "error: output '${key}' not found on stack '${stack}'. Did you run 'cd infra && cdk deploy --all'?" >&2
    exit 1
  fi
  printf '%s' "$val"
}

echo "reading live stack outputs from CloudFormation (region $REGION)…"
USER_POOL_ID=$(get_output AuthStack UserPoolId)
CLIENT_ID=$(get_output AuthStack UserPoolClientId)
CLIENT_SECRET_ARN=$(get_output AuthStack UserPoolClientSecretArn)
API_URL=$(get_output ApiStack ApiUrl)
STREAM_URL=$(get_output ApiStack StreamFunctionUrl)

CLIENT_SECRET=$(aws secretsmanager get-secret-value \
  --region "$REGION" --secret-id "$CLIENT_SECRET_ARN" \
  --query SecretString --output text)

# Preserve cookie_secret if already present; otherwise mint a fresh one.
COOKIE_SECRET=""
if [[ -f "$SECRETS_FILE" ]]; then
  COOKIE_SECRET=$(grep -E '^cookie_secret' "$SECRETS_FILE" | head -1 \
    | sed -E 's/^cookie_secret[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/' || true)
fi
if [[ -z "$COOKIE_SECRET" || "$COOKIE_SECRET" == "replace-me-32-bytes-random-hex" ]]; then
  COOKIE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

mkdir -p "$SECRETS_DIR"
cat > "$SECRETS_FILE" <<EOF
[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "$COOKIE_SECRET"
client_id = "$CLIENT_ID"
client_secret = "$CLIENT_SECRET"
server_metadata_url = "https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"
expose_tokens = ["id", "access"]

[api]
api_base_url = "$API_URL"

[runtime]
api_base_url = "$API_URL"

[stream]
stream_url = "$STREAM_URL"
EOF

echo "synced $SECRETS_FILE (user pool $USER_POOL_ID, client $CLIENT_ID)"

if [[ -x venv/bin/streamlit ]]; then
  exec ./venv/bin/python -m streamlit run streamlit_client/app.py "$@"
else
  exec streamlit run streamlit_client/app.py "$@"
fi
