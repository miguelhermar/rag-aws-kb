"""Streamlit chat client for the RAG-AWS API.

Docs consulted (Streamlit 1.57.0):
  https://docs.streamlit.io/develop/api-reference/chat/st.chat_input
  https://docs.streamlit.io/develop/api-reference/chat/st.chat_message
  https://docs.streamlit.io/develop/api-reference/connections/st.secrets
"""
from __future__ import annotations

import requests
import streamlit as st

REQUEST_TIMEOUT_S = 30

st.set_page_config(page_title="Acme Notes RAG", page_icon=":speech_balloon:")
st.title("Acme Notes — Knowledge Base")
st.caption("Ask a question; answers are grounded in the ingested sample docs.")

# Secrets are mandatory; fail loudly rather than silently sending unauthed requests.
try:
    API_BASE_URL = st.secrets["API_BASE_URL"].rstrip("/")
    API_KEY = st.secrets["API_KEY"]
except (KeyError, FileNotFoundError):
    st.error("Missing `API_BASE_URL` or `API_KEY` in `.streamlit/secrets.toml`.")
    st.stop()

with st.sidebar:
    st.header("Settings")
    top_k = st.slider("top_k", min_value=1, max_value=10, value=5)
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []  # list[dict]: {role, content, payload?}


def _confidence_badge(c: float) -> None:
    label = f"Confidence: {c:.2f}"
    if c >= 0.7:
        st.success(label)
    elif c >= 0.4:
        st.warning(label)
    else:
        st.error(label)


def _render_assistant(payload: dict) -> None:
    st.markdown(payload.get("answer", ""))
    _confidence_badge(float(payload.get("confidence", 0.0)))
    sources = payload.get("sources") or []
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})"):
        for i, s in enumerate(sources, start=1):
            st.markdown(f"**[{i}] {s.get('document', '(unknown)')}**")
            st.caption(f"`{s.get('s3_uri', '')}` — score {float(s.get('score', 0.0)):.3f}")
            st.write(s.get("snippet", ""))
            if i < len(sources):
                st.divider()


def _query_api(question: str, k: int) -> tuple[int, dict | None, str | None]:
    """Return (status_code, json_body, error_message)."""
    try:
        resp = requests.post(
            f"{API_BASE_URL}/query",
            headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
            json={"question": question, "top_k": k},
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as e:
        return 0, None, f"Network error: {e}"
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body, None


# Replay history.
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant" and msg.get("payload"):
            _render_assistant(msg["payload"])
        else:
            st.markdown(msg["content"])

prompt = st.chat_input("Ask a question about Acme Notes…")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Querying knowledge base…"):
            status, body, net_err = _query_api(prompt, top_k)
        if net_err:
            st.error(net_err)
            st.session_state.messages.append({"role": "assistant", "content": net_err})
        elif status == 200 and isinstance(body, dict):
            _render_assistant(body)
            st.session_state.messages.append(
                {"role": "assistant", "content": body.get("answer", ""), "payload": body}
            )
        else:
            msg = (body or {}).get("message", "Unknown error")
            err_label = (body or {}).get("error", "Error")
            text = f"API error {status} ({err_label}): {msg}"
            st.error(text)
            st.session_state.messages.append({"role": "assistant", "content": text})
