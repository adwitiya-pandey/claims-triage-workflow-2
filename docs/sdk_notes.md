# SDK notes

Pre-filled on 17 Oct 2026 and re-verified on 19 Oct 2026 against installed packages: langgraph 1.2.16, langgraph-checkpoint-sqlite 3.1.2, google-genai 2.31.0, mcp 2.3.0, pydantic 2.14.1, pytest 9.1.1.

## LangGraph and the SQLite checkpointer (verified)
```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
```
- `interrupt(value, *, response_schema=None)`: never pass `response_schema`.
- `SqliteSaver(conn: sqlite3.Connection, *, serde=None)`. For use from API threads: `sqlite3.connect(path, check_same_thread=False)`. `setup()` is called automatically; tables created: `checkpoints`, `writes`. `SqliteSaver.from_conn_string` also exists.
- Paused `graph.invoke(...)` returns the state plus `"__interrupt__": [Interrupt(value=..., id=...)]`; payload = `result["__interrupt__"][0].value`.
- `graph.get_state(config).next` is `('review',)` while paused and `()` when finished.
- **Restart safety (verified):** pause on one `SqliteSaver`, close the connection, build a *new* graph object on a *new* connection to the same file, `invoke(Command(resume={"action": "CONFIRM"}), cfg)` with the same `thread_id`: the run completed and `review` received the resume value.
- **State on finished thread across restarts (verified):** when a thread finishes before a restart, `get_state(cfg).values` holds the final state dictionary and `get_state(cfg).next` is empty tuple `()`.

## google-genai (verified)
- `types.GenerateContentConfig` has `system_instruction`, `response_mime_type`, `response_json_schema`, `response_schema`, `temperature`, `max_output_tokens`. Use `response_json_schema` with a JSON-schema dict, as SPEC 10 says.
- `response.usage_metadata` fields: `prompt_token_count`, `candidates_token_count`, `total_token_count`, `thoughts_token_count`, `cached_content_token_count` (any may be `None`; treat `None` as 0). `response.text` exists.
- `google.genai.errors`: `APIError` (`.code`, `.status`, `.message`), `ClientError` (4xx), `ServerError` (5xx).
- `gemini-3.8-flash` is listed as Stable on the official models page. Free-tier limits: 15 RPM / 1M TPM / 1500 RPD on Google AI Studio free tier. Exponential backoff with jitter handles transient 429 rate limits.

## mcp client (verified; Project A's wire format is in A's SPEC 8)
```python
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
```
- The HTTP client class the SDK imports is `httpx2` (or standard `httpx.AsyncClient`).
- Project A HTTP Bearer check: Project A expects an HTTP header `Authorization: Bearer <token>`. No custom authentication handshake is required.
- `await session.call_tool(name, args)` returns an object with `.is_error`, `.content` (text items), `.structured_content`.
- Tool errors are *results* with `is_error=True` and text starting with the code. HTTP 401 surfaces as a transport exception.

## Probe snippet (re-run and verified)
```python
import sqlite3, tempfile, os
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver


class S(TypedDict, total=False):
    x: str
    review: dict


def a(s):
    return {"x": "a"}


def review(s):
    return {"review": interrupt({"packet": "p1"})}


def build(cp):
    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("review", review)
    g.add_edge(START, "a")
    g.add_edge("a", "review")
    g.add_edge("review", END)
    return g.compile(checkpointer=cp)


path = os.path.join(tempfile.mkdtemp(), "cp.db")
cfg = {"configurable": {"thread_id": "t-1"}}
c1 = sqlite3.connect(path, check_same_thread=False)
g1 = build(SqliteSaver(c1))
r = g1.invoke({"x": "in"}, cfg)
print(r["__interrupt__"][0].value, g1.get_state(cfg).next)
c1.close()
c2 = sqlite3.connect(path, check_same_thread=False)
g2 = build(SqliteSaver(c2))
print(g2.invoke(Command(resume={"action": "CONFIRM"}), cfg), g2.get_state(cfg).next)
```
