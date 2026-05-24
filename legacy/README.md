# Legacy prototype (reference only)

This directory contains the **original** RAG prototype that this project was built on. It is preserved unchanged so that the production AWS implementation in `infra/`, `lambda/`, and `streamlit_client/` can be diffed against it.

**Do not run, deploy, or depend on anything in here.** The production stack does not use this code at runtime. Two pieces of logic were *ported* (not imported) from here into the new Lambda handler:

- `legacy/rag/generator.py` — grounded prompt shape (stripped down)
- `legacy/rag/generator.py` — confidence-scoring formula (simplified)
- `legacy/backend/models/query.py` — request/response schema seed

The original README is at [README-original.md](README-original.md) and describes how the prototype was meant to be run with Google Gemini + FAISS. Treat it as historical.

Notes:
- The prototype's `.env` (since gitignored) contained a leaked Google Gemini API key. The production stack does not use Gemini at all.
- The prototype's FAISS index lived at `vectorstore/` and uploaded documents at `data/`. Both were local-only and were removed during the legacy move.
