import os
import sys
from pathlib import Path

LAMBDA_DIR = Path(__file__).resolve().parent.parent / "lambda"
if str(LAMBDA_DIR) not in sys.path:
    sys.path.insert(0, str(LAMBDA_DIR))

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("KB_ID", "TESTKBID00")
os.environ.setdefault(
    "MODEL_ARN", "anthropic.claude-3-haiku-20240307-v1:0"
)
