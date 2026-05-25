from __future__ import annotations

import base64
import json
import time
import uuid

import requests
import streamlit as st

REQUEST_TIMEOUT_S = 30
TOKEN_REFRESH_LEEWAY_S = 60

st.set_page_config(page_title="Acme Notes RAG", page_icon=":speech_balloon:")
st.title("Acme Notes — Knowledge Base")
st.caption("Ask a question; answers are grounded in the ingested sample docs.")

try:
    API_BASE_URL = st.secrets["api"]["api_base_url"].rstrip("/")
except (KeyError, FileNotFoundError):
    st.error("Missing `[api].api_base_url` in `.streamlit/secrets.toml`.")
    st.stop()

# Phase 9a — streaming Lambda Function URL. Optional (the toggle hides gracefully
# if absent), but required to use the "Stream responses" checkbox below.
try:
    STREAM_URL = st.secrets["stream"]["stream_url"].rstrip("/")
except (KeyError, FileNotFoundError):
    STREAM_URL = ""


if not st.user.is_logged_in:
    st.info("Sign in with your Cognito account to continue.")
    st.button("Log in with Cognito", on_click=st.login)
    st.stop()


def _jwt_exp(token: str) -> int:
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    return int(payload["exp"])


def _id_token() -> str:
    token = st.user.tokens["id"]
    if _jwt_exp(token) - int(time.time()) < TOKEN_REFRESH_LEEWAY_S:
        st.logout()
        st.rerun()
    return token


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_id_token()}", "Content-Type": "application/json"}


def _new_session_id() -> str:
    return "s-" + uuid.uuid4().hex


def _confidence_badge(c: float) -> None:
    label = f"Confidence: {c:.3f}"
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


def _handle_unauthorized() -> None:
    st.session_state.messages = []
    st.session_state.pop("session_id", None)
    st.session_state.pop("conversations", None)
    st.error("Session expired, please log in again.")
    st.button("Log out", on_click=st.logout, key="reauth_logout")
    st.stop()


def _list_conversations() -> list[dict]:
    try:
        r = requests.get(
            f"{API_BASE_URL}/conversations",
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException:
        return []
    if r.status_code in (401, 403):
        _handle_unauthorized()
    if r.status_code != 200:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    return data if isinstance(data, list) else data.get("conversations", []) or []


def _load_conversation(session_id: str) -> list[dict]:
    try:
        r = requests.get(
            f"{API_BASE_URL}/conversations/{session_id}",
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as e:
        st.error(f"Network error: {e}")
        return []
    if r.status_code in (401, 403):
        _handle_unauthorized()
    if r.status_code != 200:
        st.error(f"Failed to load conversation ({r.status_code}).")
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    return data.get("messages", []) if isinstance(data, dict) else []


# ---------------------------------------------------------------------------
# Phase 9a — streaming SSE consumer
# ---------------------------------------------------------------------------
def _iter_sse_events(resp):
    """Yield (event_name, data_dict) tuples from a streaming requests.Response.

    Parses the standard SSE wire format: lines of "event: NAME" + "data: JSON"
    separated by blank lines.
    """
    event_name = "message"
    data_lines: list[str] = []
    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                blob = "\n".join(data_lines)
                try:
                    payload = json.loads(blob)
                except ValueError:
                    payload = {"raw": blob}
                yield event_name, payload
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            # SSE comment / keepalive
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())


def _stream_query(question: str, session_id: str, k: int, placeholder):
    """POST to the streaming Function URL and incrementally render tokens.

    Returns (final_payload_dict_or_None, error_message_or_None) so the caller
    can append the right entry to st.session_state.messages.
    """
    if not STREAM_URL:
        return None, "Streaming is enabled but [stream].stream_url is not set in secrets.toml."

    url = f"{STREAM_URL}/query-stream"
    body = {"question": question, "session_id": session_id, "top_k": k}
    try:
        resp = requests.post(
            url,
            headers={**_auth_headers(), "Accept": "text/event-stream"},
            json=body,
            timeout=REQUEST_TIMEOUT_S,
            stream=True,
        )
    except requests.RequestException as e:
        return None, f"Network error: {e}"

    if resp.status_code in (401, 403):
        return None, "UNAUTHORIZED"
    if resp.status_code != 200:
        try:
            return None, f"Stream error {resp.status_code}: {resp.text[:200]}"
        except Exception:
            return None, f"Stream error {resp.status_code}"

    accumulated_text = ""
    sources = []
    final_meta = {}
    error_payload = None

    for event_name, data in _iter_sse_events(resp):
        if event_name == "meta":
            # not user-visible
            continue
        if event_name == "token":
            accumulated_text += data.get("text", "")
            placeholder.markdown(accumulated_text + " ▌")
        elif event_name == "sources":
            sources = data.get("sources") or []
        elif event_name == "done":
            final_meta = data
            break
        elif event_name == "error":
            error_payload = data
            break

    if error_payload:
        return None, f"Stream error: {error_payload.get('message', 'unknown')}"

    # If the model emitted INSUFFICIENT_CONTEXT the streaming Lambda
    # appended the canned answer to the token stream and clamps confidence
    # <=0.2 in the `done` event. Honour that: strip the marker prefix from
    # the visible text so the user only sees the canned answer.
    INSUFFICIENT_MARKER = "INSUFFICIENT_CONTEXT"
    if accumulated_text.strip().startswith(INSUFFICIENT_MARKER):
        # The Lambda appends "\n\n" + canned to the original stream.
        if "\n\n" in accumulated_text:
            accumulated_text = accumulated_text.split("\n\n", 1)[1]
        else:
            accumulated_text = "I don't have information about that in the knowledge base."
        placeholder.markdown(accumulated_text)
    else:
        placeholder.markdown(accumulated_text)

    payload = {
        "answer": accumulated_text,
        "confidence": float(final_meta.get("confidence", 0.0)),
        "sources": sources,
        "conversation_name": final_meta.get("conversation_name"),
        "metadata": {
            "model": final_meta.get("model_id", ""),
            "retrieval_strategy": "bedrock-kb-s3vectors-titan-v2-topk-stream",
            "request_id": "",
            "latency_ms": int(final_meta.get("latency_ms", 0)),
        },
    }
    return payload, None


def _post_query(question: str, session_id: str, k: int) -> tuple[int, dict | None, str | None]:
    try:
        r = requests.post(
            f"{API_BASE_URL}/query",
            headers=_auth_headers(),
            json={"question": question, "session_id": session_id, "top_k": k},
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as e:
        return 0, None, f"Network error: {e}"
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body, None


if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = _new_session_id()
if "conversations" not in st.session_state:
    st.session_state.conversations = _list_conversations()


with st.sidebar:
    st.markdown(f"**{st.user.email}**")
    st.button("Log out", on_click=st.logout, key="sidebar_logout")
    st.divider()

    top_k = st.slider("top_k", min_value=1, max_value=10, value=5)

    # Phase 9a — streaming toggle. Default ON when a stream_url is configured.
    stream_default = bool(STREAM_URL)
    use_stream = st.checkbox(
        "Stream responses",
        value=stream_default,
        help="Use the Lambda Function URL + SSE path (faster TTFB). "
             "When off, the buffered REST /query path is used.",
        disabled=not STREAM_URL,
    )
    if not STREAM_URL:
        st.caption("Set `[stream].stream_url` in secrets.toml to enable streaming.")

    if st.button("+ New conversation"):
        st.session_state.session_id = _new_session_id()
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.subheader("Past conversations")
    for conv in st.session_state.conversations:
        sid = conv.get("session_id") or conv.get("id") or ""
        if not sid:
            continue
        label = conv.get("conversation_name") or conv.get("title") or conv.get("preview") or sid
        if st.button(label, key=f"conv-{sid}"):
            st.session_state.session_id = sid
            st.session_state.messages = _load_conversation(sid)
            st.rerun()


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
        if use_stream and STREAM_URL:
            # Streaming path: render tokens into a placeholder as they arrive,
            # then render confidence + sources after the `done` event.
            placeholder = st.empty()
            payload, err = _stream_query(
                prompt, st.session_state.session_id, top_k, placeholder
            )
            if err == "UNAUTHORIZED":
                _handle_unauthorized()
            elif err:
                placeholder.empty()
                st.error(err)
                st.session_state.messages.append({"role": "assistant", "content": err})
            elif payload:
                _confidence_badge(float(payload.get("confidence", 0.0)))
                sources = payload.get("sources") or []
                if sources:
                    with st.expander(f"Sources ({len(sources)})"):
                        for i, s in enumerate(sources, start=1):
                            st.markdown(f"**[{i}] {s.get('document', '(unknown)')}**")
                            st.caption(
                                f"`{s.get('s3_uri', '')}` — score {float(s.get('score', 0.0)):.3f}"
                            )
                            st.write(s.get("snippet", ""))
                            if i < len(sources):
                                st.divider()
                st.session_state.messages.append(
                    {"role": "assistant", "content": payload.get("answer", ""), "payload": payload}
                )
                st.session_state.conversations = _list_conversations()
        else:
            with st.spinner("Querying knowledge base…"):
                status, body, net_err = _post_query(
                    prompt, st.session_state.session_id, top_k
                )
            if net_err:
                st.error(net_err)
                st.session_state.messages.append({"role": "assistant", "content": net_err})
            elif status in (401, 403):
                _handle_unauthorized()
            elif status == 200 and isinstance(body, dict):
                _render_assistant(body)
                st.session_state.messages.append(
                    {"role": "assistant", "content": body.get("answer", ""), "payload": body}
                )
                st.session_state.conversations = _list_conversations()
            else:
                msg = (body or {}).get("message", "Unknown error")
                err_label = (body or {}).get("error", "Error")
                text = f"API error {status} ({err_label}): {msg}"
                st.error(text)
                st.session_state.messages.append({"role": "assistant", "content": text})
