from __future__ import annotations

import base64
import datetime
import json
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
import streamlit as st


def _patch_cognito_logout() -> None:
    # Cognito's /logout endpoint ignores the OIDC-standard `post_logout_redirect_uri`
    # parameter that Streamlit's logout flow sends, then treats the request as a
    # re-login and 302s to /login?...&post_logout_redirect_uri=..., which errors with
    # "Required string parameter redirect_uri is not present." Swap to Cognito's
    # proprietary `logout_uri` parameter, scoped to the origin so it matches the
    # registered logout URLs in auth_stack.py.
    try:
        from streamlit.web.server import oauth_authlib_routes
    except ImportError:
        return

    def cognito_build_logout_url(
        end_session_endpoint: str,
        client_id: str,
        post_logout_redirect_uri: str,
        id_token: str | None = None,
    ) -> str:
        parsed_redirect = urlparse(post_logout_redirect_uri)
        origin = f"{parsed_redirect.scheme}://{parsed_redirect.netloc}"
        parsed = urlparse(end_session_endpoint)
        merged = {
            **dict(parse_qsl(parsed.query)),
            "client_id": client_id,
            "logout_uri": origin,
        }
        return parsed._replace(query=urlencode(merged)).geturl()

    oauth_authlib_routes.build_logout_url = cognito_build_logout_url


_patch_cognito_logout()


REQUEST_TIMEOUT_S = 30
TOKEN_REFRESH_LEEWAY_S = 60
APP_NAME = "AWS Knowledge Base"

st.set_page_config(
    page_title=APP_NAME,
    page_icon=":books:",
    layout="centered",
    initial_sidebar_state="expanded",
)

try:
    API_BASE_URL = st.secrets["api"]["api_base_url"].rstrip("/")
except (KeyError, FileNotFoundError):
    st.error("Missing `[api].api_base_url` in `.streamlit/secrets.toml`.")
    st.stop()

# Phase 9a — streaming Lambda Function URL. Optional (the toggle hides gracefully
# if absent), but required to use the "Stream responses" checkbox.
try:
    STREAM_URL = st.secrets["stream"]["stream_url"].rstrip("/")
except (KeyError, FileNotFoundError):
    STREAM_URL = ""


# ---------------------------------------------------------------------------
# CSS — one injection block. Targets only documented stable selectors
# (data-testid="stSidebar" / stChatInput / stButton), no internal emotion
# class names. See SESSION_HANDOFF.md research note for the rationale.
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* Tighten chat-input radius + subtle border */
    [data-testid="stChatInput"] {
        border-radius: 14px;
    }

    /* Sidebar: turn each st.button into a flush, hoverable list item.
       This is what gives the ChatGPT-style conversation list its feel. */
    section[data-testid="stSidebar"] [data-testid="stButton"] > button {
        background: transparent;
        border: 1px solid transparent;
        text-align: left;
        justify-content: flex-start;
        padding: 0.4rem 0.65rem;
        font-weight: 400;
        font-size: 0.875rem;
        line-height: 1.25rem;
        width: 100%;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        transition: background-color 120ms ease;
    }
    section[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {
        background: rgba(0, 0, 0, 0.05);
        border-color: transparent;
    }
    section[data-testid="stSidebar"] [data-testid="stButton"] > button:focus:not(:active) {
        box-shadow: none;
        border-color: transparent;
    }
    /* Active conversation: subtle accent tint, NOT a saturated primary fill */
    section[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"] {
        background: rgba(16, 163, 127, 0.12);
        color: inherit;
        border: 1px solid rgba(16, 163, 127, 0.25);
        font-weight: 500;
    }
    section[data-testid="stSidebar"] [data-testid="stButton"] > button[kind="primary"]:hover {
        background: rgba(16, 163, 127, 0.18);
    }

    /* Dark-mode tweaks for sidebar hover */
    @media (prefers-color-scheme: dark) {
        section[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {
            background: rgba(255, 255, 255, 0.06);
        }
    }

    /* Time-bucket caption: smaller + muted */
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        opacity: 0.55;
        margin-top: 0.75rem;
        margin-bottom: 0.1rem;
        padding-left: 0.5rem;
    }

    /* Header action row: align trailing buttons cleanly */
    .header-actions [data-testid="stButton"] > button {
        font-size: 0.85rem;
        padding: 0.3rem 0.7rem;
    }

    /* Reduce vertical gap inside the sidebar so the list reads denser */
    section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0.25rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


if not st.user.is_logged_in:
    st.title(APP_NAME)
    st.caption("Grounded answers from your ingested documents.")
    st.info("Sign in with your Cognito account to continue.")
    st.button("Log in with Cognito", on_click=st.login, type="primary")
    st.stop()


# ---------------------------------------------------------------------------
# Auth / token helpers (unchanged from pre-redesign).
# ---------------------------------------------------------------------------
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
    items = data if isinstance(data, list) else data.get("conversations", []) or []
    # Sort most-recent-first for the sidebar list.
    items.sort(key=lambda c: int(c.get("created_at", 0) or 0), reverse=True)
    return items


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


def _bucket_label(created_at: int) -> str:
    """Group a conversation's creation date into a ChatGPT-style time bucket."""
    if not created_at:
        return "Older"
    try:
        created = datetime.datetime.fromtimestamp(int(created_at))
    except (OSError, ValueError, OverflowError):
        return "Older"
    delta_days = (datetime.datetime.now().date() - created.date()).days
    if delta_days <= 0:
        return "Today"
    if delta_days == 1:
        return "Yesterday"
    if delta_days <= 7:
        return "Previous 7 days"
    if delta_days <= 30:
        return "Previous 30 days"
    return "Older"


_BUCKET_ORDER = ("Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older")


# ---------------------------------------------------------------------------
# Streaming SSE consumer (Phase 9a — unchanged behavior).
# ---------------------------------------------------------------------------
def _iter_sse_events(resp):
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
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())


def _stream_query(question: str, session_id: str, k: int, placeholder):
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
            continue
        # Streamlit receives each token and updates the placeholder:
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

    INSUFFICIENT_MARKER = "INSUFFICIENT_CONTEXT"
    if accumulated_text.strip().startswith(INSUFFICIENT_MARKER):
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


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = _new_session_id()
if "conversations" not in st.session_state:
    st.session_state.conversations = _list_conversations()
if "top_k" not in st.session_state:
    st.session_state.top_k = 5
if "use_stream" not in st.session_state:
    st.session_state.use_stream = bool(STREAM_URL)


# ---------------------------------------------------------------------------
# Upload dialog (Phase 9b flow, now isolated from the sidebar).
# ---------------------------------------------------------------------------
_ALLOWED_EXTS = ["txt", "md", "html", "htm", "pdf", "doc", "docx", "csv", "xls", "xlsx"]
_EXT_TO_MIME = {
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
    "htm": "text/html",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "csv": "text/csv",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@st.dialog("Upload a document")
def _upload_dialog():
    st.caption(
        "Upload one file (max 50 MB). It will be ingested into the knowledge "
        "base and become queryable immediately."
    )
    uploaded = st.file_uploader(
        "Choose a file",
        type=_ALLOWED_EXTS,
        accept_multiple_files=False,
        key="upload_widget",
    )
    if uploaded is None:
        return
    size_mb = len(uploaded.getvalue()) / (1024 * 1024)
    st.caption(f"`{uploaded.name}` — {size_mb:.2f} MB")
    if not st.button("Ingest into knowledge base", key="ingest_btn", type="primary"):
        return
    if size_mb > 50:
        st.error(f"File too large ({size_mb:.1f} MB). Limit is 50 MB.")
        return
    ext = uploaded.name.rsplit(".", 1)[-1].lower()
    content_type = _EXT_TO_MIME.get(ext, "application/octet-stream")
    with st.status("Uploading and ingesting…", expanded=True) as status:
        try:
            status.write("Minting upload URL…")
            r = requests.post(
                f"{API_BASE_URL}/documents",
                headers=_auth_headers(),
                json={"filename": uploaded.name, "content_type": content_type},
                timeout=REQUEST_TIMEOUT_S,
            )
            if r.status_code in (401, 403):
                _handle_unauthorized()
            r.raise_for_status()
            mint = r.json()

            status.write(f"Uploading {size_mb:.2f} MB to S3…")
            put = requests.put(
                mint["upload_url"],
                data=uploaded.getvalue(),
                headers={"Content-Type": content_type},
                timeout=120,
            )
            put.raise_for_status()

            status.write("Starting ingestion job…")
            r = requests.post(
                f"{API_BASE_URL}/ingest",
                headers=_auth_headers(),
                json={"key": mint["key"]},
                timeout=REQUEST_TIMEOUT_S,
            )
            if r.status_code in (401, 403):
                _handle_unauthorized()
            r.raise_for_status()
            job_id = r.json()["job_id"]
            status.write(f"Job started: `{job_id}`")

            deadline = time.monotonic() + 90.0
            last_status = "STARTING"
            last_body: dict = {}
            while time.monotonic() < deadline:
                time.sleep(2.0)
                r = requests.get(
                    f"{API_BASE_URL}/ingest/{job_id}",
                    headers=_auth_headers(),
                    timeout=REQUEST_TIMEOUT_S,
                )
                if r.status_code in (401, 403):
                    _handle_unauthorized()
                if r.status_code != 200:
                    status.update(label="Polling failed", state="error")
                    st.error(f"GET /ingest/{job_id} -> {r.status_code}")
                    break
                last_body = r.json()
                last_status = last_body.get("status", "UNKNOWN")
                status.write(f"Status: `{last_status}`")
                if last_status in ("COMPLETE", "FAILED", "STOPPED"):
                    break

            if last_status == "COMPLETE":
                stats = last_body.get("statistics", {})
                status.update(label="Ingestion complete", state="complete")
                st.success(
                    f"Indexed {stats.get('indexed', 0)} / "
                    f"{stats.get('scanned', 0)} document(s); "
                    f"failed {stats.get('failed', 0)}."
                )
                st.session_state.conversations = _list_conversations()
            elif last_status in ("FAILED", "STOPPED"):
                status.update(label=f"Ingestion {last_status}", state="error")
                reasons = last_body.get("failure_reasons") or []
                st.error(
                    f"Ingestion {last_status}. "
                    + ("Reasons: " + "; ".join(reasons) if reasons else "")
                )
            else:
                status.update(label="Timed out", state="error")
                st.warning(
                    f"Did not reach COMPLETE within 90s. Last status: {last_status}."
                    f" Job id: {job_id}."
                )
        except requests.HTTPError as e:
            status.update(label="Failed", state="error")
            body_text = e.response.text[:300] if e.response is not None else ""
            st.error(f"HTTP error: {e} {body_text}")
        except requests.RequestException as e:
            status.update(label="Network error", state="error")
            st.error(f"Network error: {e}")


# ---------------------------------------------------------------------------
# Header: title + right-aligned action row (Settings popover + Upload button)
# ---------------------------------------------------------------------------
header_left, header_right = st.columns([0.62, 0.38])
with header_left:
    st.title(APP_NAME)
    st.caption("Grounded answers from your ingested documents.")
with header_right:
    st.markdown('<div class="header-actions">', unsafe_allow_html=True)
    spacer, settings_col, upload_col = st.columns([0.1, 0.45, 0.45])
    with settings_col:
        with st.popover("⚙ Settings", use_container_width=True):
            st.session_state.top_k = st.slider(
                "Sources to retrieve (top_k)",
                min_value=1,
                max_value=10,
                value=int(st.session_state.top_k),
                help="How many KB chunks to retrieve and consider as evidence.",
            )
            if STREAM_URL:
                st.session_state.use_stream = st.checkbox(
                    "Stream responses",
                    value=bool(st.session_state.use_stream),
                    help="Token-by-token streaming via Lambda Function URL + SSE.",
                )
            else:
                st.session_state.use_stream = False
                st.caption("Set `[stream].stream_url` in secrets.toml to enable streaming.")
    with upload_col:
        if st.button("⬆ Upload", use_container_width=True):
            _upload_dialog()
    st.markdown("</div>", unsafe_allow_html=True)

st.divider()


# ---------------------------------------------------------------------------
# Sidebar: conversations-only + user identity at the bottom.
# ---------------------------------------------------------------------------
with st.sidebar:
    if st.button("＋ New conversation", use_container_width=True, type="primary"):
        st.session_state.session_id = _new_session_id()
        st.session_state.messages = []
        st.rerun()

    convs = st.session_state.conversations
    if not convs:
        st.caption("No past conversations yet.")
    else:
        # Group by bucket while preserving most-recent-first order.
        grouped: dict[str, list[dict]] = {}
        for conv in convs:
            bucket = _bucket_label(int(conv.get("created_at", 0) or 0))
            grouped.setdefault(bucket, []).append(conv)
        active_sid = st.session_state.session_id
        for bucket in _BUCKET_ORDER:
            if bucket not in grouped:
                continue
            st.caption(bucket)
            for conv in grouped[bucket]:
                sid = conv.get("session_id") or conv.get("id") or ""
                if not sid:
                    continue
                label = (
                    conv.get("conversation_name")
                    or conv.get("title")
                    or conv.get("preview")
                    or "Untitled"
                )
                btn_type = "primary" if sid == active_sid else "secondary"
                if st.button(
                    label,
                    key=f"conv-{sid}",
                    use_container_width=True,
                    type=btn_type,
                ):
                    st.session_state.session_id = sid
                    st.session_state.messages = _load_conversation(sid)
                    st.rerun()

    st.divider()

    # User identity / logout. Popover keeps the surface compact like ChatGPT's
    # bottom-left avatar menu.
    with st.popover(f"👤  {st.user.email}", use_container_width=True):
        st.caption("Signed in as")
        st.markdown(f"**{st.user.email}**")
        st.divider()
        st.button("Log out", on_click=st.logout, key="sidebar_logout", use_container_width=True)


# ---------------------------------------------------------------------------
# Chat history replay
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant" and msg.get("payload"):
            _render_assistant(msg["payload"])
        else:
            st.markdown(msg["content"])

prompt = st.chat_input("Ask a question about your documents…")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    top_k = int(st.session_state.top_k)
    use_stream = bool(st.session_state.use_stream) and bool(STREAM_URL)

    with st.chat_message("assistant"):
        if use_stream:
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
                was_first_turn = len(st.session_state.messages) <= 1
                st.session_state.messages.append(
                    {"role": "assistant", "content": payload.get("answer", ""), "payload": payload}
                )
                st.session_state.conversations = _list_conversations()
                if was_first_turn:
                    st.rerun()
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
                was_first_turn = len(st.session_state.messages) <= 1
                st.session_state.messages.append(
                    {"role": "assistant", "content": body.get("answer", ""), "payload": body}
                )
                st.session_state.conversations = _list_conversations()
                if was_first_turn:
                    st.rerun()
            else:
                msg = (body or {}).get("message", "Unknown error")
                err_label = (body or {}).get("error", "Error")
                text = f"API error {status} ({err_label}): {msg}"
                st.error(text)
                st.session_state.messages.append({"role": "assistant", "content": text})
