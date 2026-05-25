import uuid

import structlog
from bedrock_agentcore import BedrockAgentCoreApp

import rag
from schemas import QueryRequest, QueryResponse

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
_logger = structlog.get_logger()

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context=None):
    request_id = str(uuid.uuid4())
    log = _logger.bind(request_id=request_id)

    req = QueryRequest.model_validate(payload)
    log.info("agent.invoke", session_id=req.session_id, actor_id=req.actor_id, top_k=req.top_k)

    result = rag.run_query(
        prompt=req.prompt,
        session_id=req.session_id,
        actor_id=req.actor_id,
        top_k=req.top_k,
        request_id=request_id,
    )
    response = QueryResponse.model_validate(result)
    log.info(
        "agent.success",
        latency_ms=response.metadata.latency_ms,
        sources=len(response.sources),
        first_turn=response.conversation_name is not None,
    )
    return response.model_dump()


if __name__ == "__main__":
    app.run()
