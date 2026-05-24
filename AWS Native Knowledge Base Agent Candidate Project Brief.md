## AWS-Native Knowledge Base Agent Productionization 

## 1) Project goal 

Build an AWS-native Knowledge Base Agent solution based on the sample RAG project provided in this project called RAG-AWS.

## **Expected explanation:** 

- README length: aim for about 2–4 pages. Bullet points are fine. 

- Design explanation: include a concise architecture summary and key tradeoffs. 

- Infrastructure: explain the CDK stacks and AWS services provisioned. 

- API contract: document the available endpoints, authentication method, request/response schema, and local Streamlit connection flow. 

- AI/RAG behavior: explain retrieval, prompting, grounding, source citation behavior, and confidence handling. 

- AI tools usage: include a short section on how you used coding assistants or LLMs while building the project. 

- Evidence: include at least one example run showing a local Streamlit question, the AWS API request, the structured response, and any retrieved sources. 

## 2) Business scenario 

A data science team has built a promising prototype knowledge base agent. The prototype works locally and demonstrates the value of retrieval-augmented generation, but it is not yet production-ready. 

The engineering team now needs to help move the prototype toward a production architecture that is: 

- deployable through infrastructure as code, 

- accessible through a secure API, 

- usable from a lightweight local or internal UI, 

- observable enough for debugging and review, 

- structured enough to support future production hardening, 

- and realistic for enterprise operation. 

Your goal is to show how you would take a sample RAG application and turn it into a credible AWS-native engineering solution. 



## 3) Reference project 

Use the following project from the directory RAG-AWS as a starting point.

The sample demonstrates a knowledge base chatbot pattern with: 

- document upload, 

- document parsing, 

- chunking, 

- embedding generation, 

- vector search, 

- answer generation, 

- source citations, 

- confidence scoring, 

- and a Streamlit-style interaction model. 

You are not required to preserve the exact implementation. The sample repository is not a strict template; it is a reference for the kind of prototype an engineering team may receive from a data science team. You may change the application structure, replace FAISS with an AWS-native retrieval option, replace the LLM provider, change the API framework, significantly refactor the code, or simplify the UI if you preserve the core RAG use case and explain your decisions. 

The important requirement is that you explain how the prototype concepts map into your AWS architecture. 

You should also include a small sample knowledge base document set as part of the final solution. The solution does not need to include a full document ingestion workflow or userfacing upload process. It is acceptable to create or seed a knowledge base with a few sample PDFs, Markdown files, text files, policy documents, FAQs, or other lightweight business documents and use those documents for the production-style demo and evaluation. The sample documents do not need to be large, but they should be realistic enough to demonstrate retrieval, grounding, and source citation behavior. 

Examples of acceptable approaches: 

- Amazon Bedrock Knowledge Bases with S3 as the document source and API Gateway/Lambda as the query API. 

- A containerized FastAPI service on ECS/Fargate with CDK-managed infrastructure and a local Streamlit client. 

- A Lambda-based API for a small scoped implementation, with clear notes on when you would move to containers. 

- An Amazon Bedrock AgentCore-based approach for hosting or operating the agent runtime, memory, tools, gateway, or observability components.

## 4) Required system capabilities 

Your solution should demonstrate the following core capabilities: 

A minimum strong submission should include: 

- CDK-defined AWS infrastructure. 

- An authenticated AWS-hosted API. 

- A pre-seeded or sample knowledge base using included sample documents. 

- A local Streamlit client that calls the AWS API with a token. 

- Structured JSON responses with an answer, sources, and metadata. 

- README instructions and at least one successful demo run. 

Document ingestion, document management, and user upload workflows are optional extensions and should not be prioritized over the core API, CDK, and Streamlit-to-AWS flow. 

1. Infrastructure as Code 

   - Use AWS CDK to define the main AWS resources. 

   - Include clear deployment instructions. 

   - The CDK app should synthesize successfully. 

   - Ideally, it should deploy successfully in a reviewer-owned AWS account, subject to model/service availability. 

2. Authenticated API 

   - Expose an API endpoint in AWS. 

   - Require a token or API authentication mechanism for calls from the local Streamlit app. 

   - Document how the token is configured and passed. 

   - Avoid hardcoding secrets in source code. 

3. Knowledge base query flow 

   - Include or generate a small sample document set for the knowledge base. 

   - Create or seed a knowledge base using those sample documents. 

   - A full document ingestion pipeline or user-facing upload process is not required. 

   - Accept a user question through an API request. 

   - Retrieve relevant knowledge base context. 

   - Generate a grounded answer. 

   - Return a structured JSON response. 

4. Local Streamlit client 

   - Provide a simple local Streamlit app or equivalent lightweight UI. 

   - The local app should connect to the AWS API endpoint using the configured token. 

   - The app should show the user question, generated answer, confidence or grounding signal, and sources if available. 

5. Productionization explanation 

- Explain how the sample project would need to change to become production-ready. 

- Discuss security, deployment, observability, cost, scaling, and operational risks. 

- Include practical production tradeoffs such as authentication model, rate limiting, private networking, document lifecycle, monitoring, cost controls, logging redaction, model access, and failure handling. 

## 5) Suggested architecture 

You may choose your own architecture, but a strong submission will usually include some version of the following: 

- Local Streamlit client 

   - Runs on the reviewer’s machine. 

   - Reads _API_BASE_URL_ and _API_TOKEN_ from local environment variables or Streamlit secrets. 

   - Calls the AWS-hosted query API. 

- API layer 

   - Amazon API Gateway or an equivalent AWS-managed entry point. 

   - Token-based authentication using an API key, Lambda authorizer, Cognito/JWT, or another clearly explained method. 

   - Endpoints such as _/health_ , _/query_ , and optionally _/ingest_ . 

- Agent or orchestration layer 

   - AWS Lambda, ECS/Fargate, Amazon Bedrock AgentCore Runtime, or another AWS compute/agent runtime layer. 

   - Handles request validation, retrieval, prompt construction, LLM invocation, response formatting, and error handling. 

- Retrieval layer 

   - Amazon Bedrock Knowledge Bases, OpenSearch Serverless, or another documented retrieval mechanism. 

   - Should support source-grounded responses. 

- Storage layer 

   - Amazon S3 for uploaded/source documents. 

   - Optional metadata store such as DynamoDB. 

- Observability layer 

   - CloudWatch logs at minimum. 

   - Optional request IDs, structured logs, latency metrics, cost notes, tracing, or Amazon Bedrock AgentCore observability features. 

- Secrets/configuration 

   - AWS Secrets Manager, SSM Parameter Store, CDK context, or environment variables. 

   - No plaintext production secrets committed to the repository. 

You do not need to implement every production feature, but your README should make clear what is implemented versus what is proposed for a production version. 

## 6) API contract 

Your API should return structured JSON. You may extend the schema, but it should be documented. 

Example request: 

{ "question": "What is the refund policy for enterprise customers?", "session_id": "optional-session-id", "top_k": 5 } 

Example response: 

{ "answer": "Enterprise customers can request a refund within the documented refund window, subject to contract terms...", "confidence": 0.84, "sources": [ { "document_id": "refund_policy.pdf", "chunk_id": "refund_policy.pdf#chunk-4", "score": 0.91, "excerpt": "Enterprise refund requests must be reviewed..." } ], "metadata": { "model": "anthropic.claude-3-5-sonnet", "retrieval_strategy": "bedrock_knowledge_base", "request_id": "abc-123", "latency_ms": 1240 } } 

Basic error responses should also be handled and documented: 

{ "error": "unauthorized", 

"message": "Missing or invalid authorization token", "request_id": "abc-123" 

} 

## 7) Authentication and security expectations 

The API must not be publicly callable without authentication. 

Acceptable authentication approaches include: 

- API Gateway API key for a lightweight demo, 

- Lambda authorizer validating a bearer token, 

- Cognito/JWT-based authentication, 

- or another clearly justified token-based method. 

Please document: 

- how the token is created or configured, 

- where it is stored, 

- how the local Streamlit app passes it, 

- how unauthorized calls are rejected, 

- and what you would improve for a production identity model. 

At minimum, do not commit real secrets, AWS credentials, or model provider keys to the repository. 

## 8) Infrastructure as Code expectations 

Use AWS CDK to define the infrastructure. 

Your CDK implementation should include, where applicable: 

- API Gateway or equivalent API entry point, 

- compute layer such as Lambda or ECS/Fargate, 

- document storage such as S3, 

- knowledge base or vector retrieval resources, 

- IAM roles and least-privilege permissions where practical, 

- logging resources, 

- configuration outputs such as API URL and bucket name, 

- and clear environment-specific configuration. 

The CDK code should be readable and organized. It does not need to be highly abstracted, but it should be understandable to a reviewer. 

Please also include cleanup instructions, such as _cdk destroy_ , and call out any resources that may incur ongoing cost if they are not removed. 

## 9) RAG and agent behavior 

Your solution should demonstrate a reasonable knowledge base query pattern. 

At minimum, document: 

- what sample documents are included in the final solution, 

- how those documents are used to create or seed the knowledge base, 

- whether document ingestion is intentionally out of scope, 

- how text is chunked or indexed, 

- how retrieval is performed, 

- how the prompt is constructed, 

- how the answer is grounded in retrieved context, 

- how sources are returned, 

- and how confidence or uncertainty is represented. 

You may implement a simple one-step RAG pipeline or a more agentic workflow. 

Examples of optional components: 

- query rewriting, 

- retrieval confidence scoring, 

- answer quality validation, 

- citation checking, 

- fallback response when context is insufficient, 

- conversation memory, 

- or human-review flags. 

Keep the implementation simple and explainable. A clear small system is better than a large unfinished system. 

## 10) Demo expectations 

Your submission should demonstrate the following flow: 

1. Deploy or synthesize the AWS infrastructure using CDK. 

2. Create or verify the knowledge base that uses the included sample documents. 

3. Configure the local environment with API URL and token. 

4. Start the local Streamlit app. 

5. Ask a question through the local UI. 

6. Show that the request is sent to the AWS-hosted API. 

7. Return and display the answer, sources, and metadata. 

8. Show logs or request IDs that help debug the flow. 

A complete demo may be a README walkthrough, screenshots, short screen recording, or notebook-style evidence. 

## 11) Evaluation 

Please include a lightweight evaluation section. 

This does not need to be a full ML benchmark. A simple table is enough. 

Include at least 5–10 sample questions and assess: 

- whether the answer was grounded in the retrieved documents, 

- whether the answer included useful sources, 

- whether the confidence or uncertainty looked reasonable, 

- whether the API behaved correctly, 

- and whether the local Streamlit client handled the response cleanly. 

Please include: 

- at least a few examples where the system performs well, 

- at least 2 examples where retrieval or generation was weak, ambiguous, or incomplete, 

- and a short reflection on what you would improve next. 

Please also include at least one basic test, script, or documented command that validates the query API contract, authentication behavior, and a successful sample query. A full test suite is not required. 

## 12) What we’re evaluating 

We care most about: 

- Architecture judgment: sensible AWS service choices and clear production path. 

- Infrastructure quality: usable CDK, clear stacks, reproducible setup, practical IAM/security decisions. 

- API design: clean request/response schema, authentication, error handling, and local client integration. 

- RAG design: grounded responses, useful retrieval, sources, and uncertainty handling. 

- Production thinking: observability, cost, scaling, deployment, secrets, and operational tradeoffs. 

- Code quality: readable structure, minimal unnecessary complexity, clear setup instructions. 

- Communication: README clarity, assumptions, tradeoffs, and explanation of changes from the sample project. 


## 13) Review rubric 

We will review submissions primarily on: 

- AWS architecture clarity. 

- CDK and infrastructure-as-code quality. 

- API design, authentication, and error handling. 

- Local Streamlit-to-AWS integration. 

- Knowledge base setup using included sample documents. 

- RAG grounding, source handling, and uncertainty behavior. 

- Production-readiness thinking around security, observability, cost, scaling, and operations. 

- Code quality, reproducibility, and README clarity. 


## 14) Deliverables 

Please submit a GitHub repository or ZIP file containing: 

1. Working solution code 

   - AWS service/API code. 

   - CDK infrastructure code. 

   - Local Streamlit app or equivalent lightweight client. 

   - Sample knowledge base documents used for demo and evaluation, preferably in a _/sample-docs_ or equivalent folder. 

2. Run instructions 

   - Local setup. 

   - Required environment variables. 

   - CDK synth/deploy commands. 

   - Streamlit startup command. 

   - Example API call using curl or Postman. 

   - Cleanup instructions, including how to tear down AWS resources and avoid ongoing cost. 

## 3. README 

   - Architecture summary. 

   - Diagram or text-based architecture flow. 

   - Explanation of AWS services used, including whether Amazon Bedrock AgentCore was considered or used. 

   - API contract. 

   - Authentication approach. 

   - RAG/agent behavior. 

   - Description of the included sample documents and how they are used in the knowledge base. 

   - Changes made from the sample project. 

   - AI tools used during development. 

   - Assumptions and known limitations. 

   - Whether data is sent to external APIs or only AWS services. 

   - Basic testing or validation approach for the API contract, authentication behavior, and one successful sample query. 

4. Evidence of execution 

   - At least one successful query from local Streamlit to AWS API. 

   - Example request/response JSON. 

   - Screenshot, logs, or short walkthrough. 

5. Evaluation summary 

   - Simple quality assessment. 

   - Good examples. 

- Failure or disagreement examples. 

- Improvement plan. 


## 16) Closing note 


Do not over-engineer the solution. A small, clean, well-explained implementation is better than a large unfinished one. 

