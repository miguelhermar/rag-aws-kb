# Native Python Lambda runtime does NOT support response streaming
# (Node.js only as of 2026-05). The supported pattern is AWS Lambda Web Adapter
# (LWA): a long-running ASGI server inside the container, with LWA as a Lambda
# extension translating Lambda Invoke into HTTP requests. With
# AWS_LWA_INVOKE_MODE=response_stream, LWA streams the HTTP response body back
# to the Function URL caller chunk-by-chunk.
#
# References (verified 2026-05-25):
#  - https://github.com/awslabs/aws-lambda-web-adapter
#  - https://docs.aws.amazon.com/lambda/latest/dg/configuration-response-streaming.html
#    (explicitly: "Lambda supports response streaming on Node.js managed runtimes.
#     For other languages, including Python, you can ... use the Lambda Web Adapter.")
FROM public.ecr.aws/docker/library/python:3.12-slim

# Lambda Web Adapter extension. Multi-arch image (x86_64 + arm64); the host
# architecture is selected by Docker buildx based on Lambda's architecture.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

WORKDIR /var/task

COPY requirements.txt /var/task/requirements.txt
RUN pip install --no-cache-dir -r /var/task/requirements.txt

COPY *.py /var/task/

# LWA contract:
#  - AWS_LWA_PORT: the port our ASGI server binds to (default 8080).
#  - AWS_LWA_INVOKE_MODE: must be "response_stream" for the Function URL to
#    propagate chunks as they are produced.
ENV AWS_LWA_PORT=8080
ENV AWS_LWA_INVOKE_MODE=response_stream
ENV AWS_LWA_READINESS_CHECK_PATH=/health-internal

CMD ["python", "-m", "uvicorn", "stream_app:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
