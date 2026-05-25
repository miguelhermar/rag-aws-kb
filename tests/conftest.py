import os
import sys
from pathlib import Path

LAMBDA_DIR = Path(__file__).resolve().parent.parent / "lambda"
if str(LAMBDA_DIR) not in sys.path:
    sys.path.insert(0, str(LAMBDA_DIR))

AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.append(str(AGENT_DIR))

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault(
    "AGENTCORE_RUNTIME_ARN",
    "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/rag_aws_agent-abc12345",
)
os.environ.setdefault("MEMORY_ID", "rag_aws_memory-TESTID")
os.environ.setdefault("CONVERSATIONS_TABLE", "test-conversations")
# Kept for the sibling agent/ test suite (KB + model env), harmless for lambda tests.
os.environ.setdefault("KB_ID", "TESTKBID00")
os.environ.setdefault("MODEL_ARN", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
os.environ.setdefault("MODEL_ID", "anthropic.claude-haiku-4-5-20251001-v1:0")
