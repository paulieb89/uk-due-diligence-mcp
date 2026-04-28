# Building MCP Servers: What the Docs Don't Make Obvious

Lessons from building FastMCP v3 servers. These apply across projects regardless of what the server does.

Last verified: April 2026, FastMCP 3.2.4, Prefab UI 0.19.0, MCP SDK 1.27.0, ext-apps 1.3.2.

---

## 0. FastMCP in 90 Seconds (Read This First)

FastMCP is meant to be simple. Most of the lessons in this file exist because we tried to be clever and fought the framework. Before you reach for `ToolResult`, custom serialisation, or middleware tricks, **try the obvious thing first** — it usually works. This intro is here so future you (or future agents) don't repeat the mistakes.

### The mental model

A FastMCP server is a container for three kinds of components plus optional cross-cutting bits:

```
┌─────────────────────────────────────────────────────────────┐
│  FastMCP("MyServer")                                        │
│                                                             │
│  Components (the things clients can use)                    │
│  ├─ @mcp.tool         — actions the LLM invokes              │
│  ├─ @mcp.resource     — passive data clients READ            │
│  └─ @mcp.prompt       — message templates for LLM workflows  │
│                                                             │
│  Cross-cutting (optional)                                   │
│  ├─ middleware        — log/timing/auth around every call    │
│  ├─ providers         — dynamic component sources            │
│  ├─ transforms        — modify how components are exposed    │
│  └─ lifespan          — startup/shutdown hooks               │
└─────────────────────────────────────────────────────────────┘
```

The framework handles serialisation, schema generation, MCP protocol envelopes, sessions, content blocks, structured output — **all of it**. Your job is to write a Python function that returns data. FastMCP does the rest.

FastMCP response types — when to use which
Return type	Wire shape	Use when	Don't use when
-> str	{"result": "string"} (generic wrapper schema)	Single text blob, markdown, plaintext	You actually have structured data — auto-wrap forces caller to re-parse
-> int / float / bool	{"result": <value>}	Single primitive — count, ID, flag	Almost never the only thing you return; usually you want context fields too
-> dict	structured_content: {...} + matching text block	The default for any structured response. Multiple fields, nested data, JSON-shaped	Anything else — you almost always want this
-> list[X]	{"result": [...]}	Plain lists of records	When list shape really should be a top-level object with metadata
-> PydanticModel	Schema generated from model fields, structured_content from model_dump()	Strict validation with documented field names	Just internal data — dict is simpler
-> bytes	Base64-encoded blob content block	Binary file output (PDFs, images)	Text — use str
-> fastmcp.Image / Audio	Native MCP image/audio content blocks	Generated images, audio synthesis	Anything not actually media
-> ToolResult(content=, structured_content=, meta=)	Whatever you put in each field	Need to separate human-readable text from machine data, or attach runtime meta	Default tool returns — dict does this for free
For resources (which are different from tools — they're read-only data sources):

Return type	Use when
-> str	Text content (default mime: text/plain) — most common
-> bytes	Binary content (default mime: application/octet-stream) — files, images
-> ResourceResult(...)	Multiple content items, custom MIME types, item-level metadata
For resources you serialise complex data to JSON yourself with json.dumps() — resources are intentionally lower-level than tools because they're meant to be raw data delivery.

Two practical rules:

Default to -> dict for tools that return data. Don't reach for anything fancier without a specific reason.
Tools = actions, resources = data fetches. If your "tool" looks like "fetch file at this URI" with no real logic, it should be a resource template (@mcp.resource("foo://{id}")), not a tool.

### Tools vs resources — pick the right primitive

This is the single most common mistake when starting out, and it cascades into a tower of pain when you get it wrong.

| Use a tool when… | Use a resource when… |
|------------------|----------------------|
| The client invokes an **action** (search, calculate, transform, send) | The client **reads data** by URI (file, document, record) |
| Parameters meaningfully change behaviour, not just identify what to fetch | Parameters identify *which* data, like path parameters in REST |
| There may be side effects, even read-only ones (logging, rate-limiting, billing) | The data is the same every time given the same URI |
| Output shape varies based on the call | Output shape is fixed (text, JSON, bytes) |

**Concrete example from our codebase:** `case_law_search(query="negligence")` is a tool — it's a search action. But `case_law_get_judgment(uri="uksc/2024/12")` looks like an action and is currently a tool, but conceptually it's "give me the file at this URI" — a resource template would be cleaner: `judgment://{court}/{year}/{number}`. We left it as a tool for backward compatibility.

If you find yourself fighting tool output schemas for a tool that's "just" returning a file or record, **stop and consider whether it should be a resource**.

### How to return data from a tool — the simple table

```python
from fastmcp import FastMCP

mcp = FastMCP("MyServer")

@mcp.tool
def add(a: int, b: int) -> int:
    return a + b                                # primitive: returns 8
```

| Return type annotation | What FastMCP does | When to use |
|------------------------|-------------------|-------------|
| `-> int`, `-> str`, `-> bool`, `-> float` | Wraps in `{"result": <value>}` and emits a generic outputSchema | Single primitive value — counts, IDs, names, simple text |
| `-> dict` | Treats as structured object output, auto-generates a `{"type": "object"}` outputSchema, emits `structured_content` AND a JSON text content block | **Default choice for any structured response** — JSON-shaped data, multiple fields, nested records |
| `-> list[X]` | Wraps in `{"result": [...]}` like primitives, with the list as structured output | Lists of records or values |
| `-> SomePydanticModel` | Generates schema from the model fields, emits structured_content from `model_dump()` | When you want strict validation and named fields documented in the schema |
| `-> ToolResult(...)` | Explicit control: `content=`, `structured_content=`, `meta=` | **Only when you genuinely need explicit control** — custom serialisation (YAML, markdown), separate human-readable text from machine data, runtime metadata |

**The default is `-> dict`.** Resist the urge to wrap in `json.dumps()` or reach for `ToolResult` until you have a specific reason. Every time we've tried to be clever with output formats, we've broken something. See lesson 33.

### How to handle large responses — let the caller decide

Don't add server-side response-limiting middleware to "protect LLM context". Two reasons:

1. **It breaks the protocol.** FastMCP's `ResponseLimitingMiddleware` (or any equivalent) drops `structured_content` when truncating, which fails strict MCP clients that validate against the advertised outputSchema. The middleware's source comments even acknowledge this trade-off — it sets `meta={}` to skip in-process validation, but that workaround doesn't help wire-format clients.
2. **The tool author knows what to cut.** Per-tool truncation via a `max_chars` parameter (or similar) lets the tool truncate at a meaningful boundary, keep the response a valid object, and tell the caller it was truncated via a flag. Caller can re-request with a higher limit if needed.

Pattern:

```python
@mcp.tool
async def get_big_document(uri: str, max_chars: int = 50000) -> dict:
    raw = await fetch(uri)
    truncated = len(raw) > max_chars
    content = raw[:max_chars] + "\n<!-- ...truncated -->" if truncated else raw
    return {
        "uri": uri,
        "content": content,
        "truncated": truncated,
        "original_length": len(raw),
    }
```

### What to avoid

- **`return json.dumps({...})` with a `-> str` annotation.** Looks equivalent to returning a dict, isn't. Hits the generic-string wrapper schema and the result is harder to consume programmatically. Just return the dict.
- **Server-side response-truncating middleware.** As above — silently breaks structured output. Truncate at the tool level instead.
- **Reaching for `ToolResult` when a dict would do.** You almost never need it.
- **Hand-rolling output schemas with `output_schema=`.** FastMCP generates them from your return type annotation. Override only when the auto-generated schema is genuinely wrong (rare).
- **Treating MCP sessions as conversation state.** They're transport sessions, not application state. Use `stateless_http=True` for any data-fetching server (lesson 2).
- **Wrapping every tool with a `response_format: "markdown" | "json"` parameter.** Just return structured data — clients can format. Multi-format tools double your test surface and they always pick the wrong format anyway.

### When a tool isn't working through claude.ai but works locally

Check, in this order:

1. **Did you advertise an outputSchema your response doesn't actually satisfy?** Run the tool directly and inspect the response — if `structuredContent` is missing while `tools/list` advertises an object schema, you've hit lesson 33.
2. **Is response-limiting middleware truncating your response?** Check `fly logs` for warnings like `Tool {name} response exceeds size limit`. The middleware silently drops `structured_content` to fit.
3. **Is `stateless_http` set?** Without it, every second request from claude.ai fails with `Session not found`. See lesson 2.
4. **Are you wrapping outbound proxy responses?** If your server uses `create_proxy()` to call other MCP servers, install `zstandard` so httpx can decode Fly's edge-compressed responses. See lesson 30.

### TL;DR

```python
@mcp.tool
async def my_tool(param: str) -> dict:
    """Concise description of what this tool does."""
    data = await fetch_or_compute(param)
    return {"result_field": data, ...}    # framework handles the rest
```

If you find yourself doing more than this, ask whether you're solving a real problem or fighting the framework. Usually it's the latter.

---

## 1. The Constructor vs run() Split

FastMCP v3 removed transport/server settings from the `FastMCP()` constructor. The constructor is for identity and behaviour. Transport config goes on `run()` or `http_app()`.

```python
# Wrong — stateless_http was removed from the constructor in v3
mcp = FastMCP("My Server", stateless_http=True)

# Right — transport settings on run()
mcp = FastMCP("My Server", instructions="...")
mcp.run(transport="http", host="0.0.0.0", port=8080, stateless_http=True)
```

The docs are inconsistent here. The HTTP deployment page still shows a constructor example. The v3 upgrade guide and API reference are the safer source of truth. When in doubt, put transport config on `run()`.

**What goes on the constructor:** `name`, `instructions`, `auth`
**What goes on run() / http_app():** `transport`, `host`, `port`, `stateless_http`, `path`

## 2. Stateless vs Stateful HTTP

By default, FastMCP's HTTP transport maintains server-side sessions in memory. This is a problem if you:

- Deploy behind a load balancer (Fly.io, Railway, etc.)
- Run multiple instances
- Don't need conversation state between tool calls

MCP clients using `fetch()` don't properly forward `Set-Cookie` headers, so sticky sessions don't work reliably.

**Use `stateless_http=True` when:**
- Your tools are independent (each call stands alone)
- You're deploying to any platform that might scale horizontally
- You don't use elicitation, sampling, or multi-turn tool flows

**Use stateful (default) when:**
- Tools need to share state within a session
- You need elicitation (asking the user questions mid-tool)
- You're running a single local instance

Most data-fetching servers should be stateless. Most interactive/conversational servers need state.

## 3. Context Type Annotation Is Mandatory

If your tool function takes a `ctx` parameter without a `Context` type annotation, FastMCP treats it as a regular tool parameter and exposes it in the schema. The MCP client then fails with a Pydantic validation error ("Missing required argument: ctx") because it never sends a `ctx` value.

```python
# Broken — ctx appears as a required parameter in the tool schema
async def govuk_search(params: SearchInput, ctx) -> str:

# Works — FastMCP recognises Context and injects it automatically
async def govuk_search(params: SearchInput, ctx: Context) -> str:
```

The preferred pattern in FastMCP v3 is `CurrentContext()` as a default value. The bare type-hint approach (`ctx: Context`) still works but is labelled "legacy" in the docs:

```python
from fastmcp.dependencies import CurrentContext

# Preferred (v3)
async def govuk_search(params: SearchInput, ctx: Context = CurrentContext()) -> str:

# Legacy (still works)
async def govuk_search(params: SearchInput, ctx: Context) -> str:
```

The parameter name doesn't matter — only the `Context` type hint. This applies to tools, resources, and prompts.

## 4. Lifespan Context Access

When using a lifespan to share resources (e.g. an httpx client), access the yielded dict via `ctx.lifespan_context`, not `ctx.request_context.lifespan_state`.

```python
# Wrong — MCP SDK path, doesn't work with FastMCP Context (especially stateless mode)
def _client(ctx) -> httpx.AsyncClient:
    return ctx.request_context.lifespan_state["client"]

# Right — FastMCP's API
def _client(ctx: Context) -> httpx.AsyncClient:
    return ctx.lifespan_context["client"]
```

FastMCP v3 also introduced a `@lifespan` decorator (from `fastmcp.server.lifespan`) that supports composition via `|`. The older `@asynccontextmanager` pattern still works under backwards compatibility:

```python
from fastmcp.server.lifespan import lifespan

@lifespan
async def app_lifespan(server):
    async with httpx.AsyncClient() as client:
        yield {"client": client}
```

## 5. Claude Code Client Config: Type Is "http"

For remote MCP servers using Streamable HTTP, the `.mcp.json` type must be `"http"` — not `"url"`, not `"sse"`.

```json
{
  "mcpServers": {
    "my-server": {
      "type": "http",
      "url": "https://my-server.fly.dev/mcp"
    }
  }
}
```

`"sse"` is for the older SSE transport. `"stdio"` is for local subprocess servers. `"url"` is not a valid type and will silently fail to connect.

## 6. Multi-Machine Failure Mode Is 404, Not "Session Expired"

Section 2 explains why `stateless_http=True` matters. Here's the specific failure mode if you forget it:

When Fly.io (or any load balancer) routes a request to a different machine than the one that created the session, the server returns **404 Not Found** — not a session error. In the logs you see alternating 200s and 404s across different machine IDs. The session ID exists on machine A but machine B has never seen it.

```
Machine d8d374: POST /mcp → 200 OK      (session created here)
Machine e82edd: POST /mcp → 404 Not Found (session doesn't exist here)
Machine d8d374: POST /mcp → 200 OK
Machine e82edd: POST /mcp → 404 Not Found
```

Also: `auto_stop_machines = "stop"` with `min_machines_running = 0` (scale to zero) means sessions are lost when machines stop. Stateless mode makes scale-to-zero safe.

## 7. ui:// Resources Get Automatic MIME Type (Apps-only)

Do NOT manually set `mime_type="text/html;profile=mcp-app"` on `ui://` resources. FastMCP does this automatically for any resource with the `ui://` scheme.

```python
# Wrong — manual MIME type
@mcp.resource("ui://my-app/view.html", mime_type="text/html;profile=mcp-app")

# Right — let FastMCP handle it
@mcp.resource("ui://my-app/view.html")
```

On resources, `AppConfig` should only contain display settings: `csp`, `permissions`, `domain`, `prefers_border`. Don't set `resource_uri` or `visibility` on resources — those are tool-only fields.

## 8. The ext-apps SDK Version Gap (Apps-only)

The FastMCP documentation references `@modelcontextprotocol/ext-apps@0.4.0`. The actual npm package is at 1.3.2 (as of March 2026). Both work. The import path `app-with-deps` is stable across versions.

```html
<!-- FastMCP docs show this -->
import { App } from "https://unpkg.com/@modelcontextprotocol/ext-apps@0.4.0/app-with-deps";

<!-- Actual current version -->
import { App } from "https://unpkg.com/@modelcontextprotocol/ext-apps@1.3.2/app-with-deps";
```

**Always pin to a specific version.** Check `npm view @modelcontextprotocol/ext-apps version` at build time. Use unpkg.com, not esm.sh (esm.sh has unpredictable redirects and caching).

## 9. Handler Registration Order Matters (Apps-only)

In the ext-apps client SDK, register ALL handlers BEFORE calling `app.connect()`. If you register after connect, you'll miss the initial events.

```javascript
const app = new App({ name: "My App", version: "1.0.0" });

// All handlers first
app.ontoolinput = (params) => { /* ... */ };
app.ontoolresult = (result) => { /* ... */ };
app.onhostcontextchanged = (ctx) => { /* ... */ };
app.onteardown = async () => ({});

// Connect last
await app.connect();
```

The `ontoolresult` handler receives `result.structuredContent` — this is where your tool's returned dict ends up. The `result.content` array has the text fallback.

## 10. structuredContent Is Automatic for Dicts (Apps-only)

When a FastMCP tool returns a dict, FastMCP automatically converts it to `structuredContent` on the wire. You don't need to manually construct `CallToolResult` objects with `structuredContent` fields.

```python
@mcp.tool(app=AppConfig(resource_uri="ui://my-app/view.html"))
async def my_tool() -> dict:
    return {"kind": "data", "values": [1, 2, 3]}
    # FastMCP converts this to structuredContent automatically
```

However, the model only sees the text `content`, not `structuredContent`. If you return a bare dict, the model may not know what was displayed. For app tools where the model needs to understand the output, consider returning both text and structured data. Check FastMCP docs for the `ToolResult` pattern.

## 11. CORS Is Almost Never Needed

FastMCP docs say: "Most MCP clients don't need CORS." This is correct. Claude Desktop, Claude Code, claude.ai, ChatGPT — none of these need CORS on your server.

CORS is only needed for:
- MCP Inspector (browser-based testing tool)
- Custom browser apps connecting directly to your `/mcp` endpoint
- Browser-based MCP clients you build yourself

If you do need it:

```python
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],  # Never use "*" in production
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["mcp-protocol-version", "mcp-session-id", "Authorization", "Content-Type"],
        expose_headers=["mcp-session-id"],
    )
]
app = mcp.http_app(middleware=middleware)
```

The `mcp-protocol-version` and `mcp-session-id` headers are MCP-specific and must be explicitly allowed.

## 12. Custom Routes Live at Root, MCP at /mcp

When using `@mcp.custom_route()`, your routes are served at the domain root. MCP protocol is at `/mcp`. This is useful for health checks, demo pages, and static assets.

```python
@mcp.custom_route("/", methods=["GET"])
async def index(request):
    return FileResponse("index.html")

@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})
```

This means one server can serve:
- `/mcp` — MCP protocol endpoint (for AI clients)
- `/` — A demo page (for browsers)
- `/health` — Health check (for deployment platforms)

Standardise on `/mcp` (no trailing slash) in all documentation and client configs.

## 13. Local Clients vs Remote Clients

**Local (stdio):** Claude Desktop, Claude Code, VS Code, Windsurf, Cursor

The client spawns your server as a subprocess. Communication is over stdin/stdout. Each client gets its own process. No network involved.

```python
mcp.run()  # stdio is the default
```

Config: `claude_desktop_config.json` or `claude mcp add` or `.mcp.json`

**Remote (HTTP):** claude.ai, chatgpt.com, any web-based client

The client connects to your server over HTTP. Multiple clients share the same server instance. Stateless mode means each request is independent.

```python
mcp.run(transport="http", host="0.0.0.0", port=8080, stateless_http=True)
```

Config: point the client at `https://your-server.fly.dev/mcp`

**The same server code handles both.** The entry point switches transport based on a flag:

```python
def main():
    import sys
    if "--stdio" in sys.argv:
        mcp.run()
    else:
        mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), stateless_http=True)
```

## 14. fastmcp install for Local Clients

For local client setup, `fastmcp install` is cleaner than manual JSON config:

```bash
# Claude Desktop
fastmcp install claude-desktop server.py --with my-package --env API_KEY=...

# Cursor
fastmcp install cursor server.py --with my-package --env API_KEY=...
```

Supported targets: `claude-desktop`, `cursor`, `gemini-cli`, `goose`, `mcp-json`, `stdio`. Claude Code is NOT a target — configure it via `.mcp.json` or `claude mcp add` instead.

This handles dependency resolution, environment isolation, and config file management. Manual config (editing JSON) still works and is documented, but `fastmcp install` is the recommended path for supported clients.

## 15. Images Don't Flow Through MCP Tools

If a user drags photos into Claude Desktop or claude.ai, those images go to the model directly — they don't pass through MCP tool calls. Your MCP tool cannot receive the user's uploaded images.

For image-dependent workflows:
- **Use URLs.** If images are hosted (Rightmove listings, S3, etc.), pass URLs to your tools.
- **Let the model describe what it sees.** The model has the images. It can describe them in the tool arguments as text.
- **Don't use base64.** Encoding images as base64 strings in tool parameters is technically possible but wastes context and is slow.

The model sees the images. Your tools see structured data. Design accordingly.

## 16. ChatGPT MCP Support Is Beta

As of March 2026, OpenAI's MCP support is in beta on workspace/enterprise plans. The setup uses "Developer mode" and "Apps" in the ChatGPT web interface — not "Actions" (that's the older plugin/function-calling system).

Check:
- https://gofastmcp.com/integrations/chatgpt
- OpenAI's current docs (terminology shifts frequently)

The MCP endpoint URL is the same as for claude.ai — just point it at your Fly.io deployment.

See also: lessons 34 (ResourcesAsTools trap), 35 (dual-surface pattern), 36 (connector-level tool caching), 37 (search + fetch for deep research), 38 (ResponseCachingMiddleware during development).

## 17. CSP on Resources, Not on Tools (Apps-only)

Content Security Policy configuration goes on the `@mcp.resource()` decorator, not on the tool. Specifically, it goes inside the `AppConfig` on the resource's `app` parameter:

```python
@mcp.resource(
    "ui://my-app/view.html",
    app=AppConfig(
        csp=ResourceCSP(
            resource_domains=["https://unpkg.com"],    # scripts, styles, fonts
            connect_domains=["https://api.example.com"], # fetch, XHR, WebSocket
        ),
    ),
)
def my_view() -> str:
    return html_content
```

If your dashboard HTML loads the ext-apps SDK from unpkg.com, you need `resource_domains=["https://unpkg.com"]`. If it makes API calls, you need `connect_domains`. If it loads nothing external, you need no CSP at all.

## 18. MCP Server Architecture: Tool Layer, Not Agent Layer

An MCP server should be a tool layer — it fetches data and formats output. It should NOT contain its own AI agents or make LLM calls internally.

```
# Wrong — agents inside tools
Claude → calls MCP tool → tool spawns GPT agent → agent calls another tool → returns

# Right — tools return data, LLM does the thinking
Claude → calls MCP tool → tool fetches data → returns to Claude → Claude thinks
```

If your MCP tool imports `pydantic-ai`, `langchain`, `openai`, or any LLM SDK, you're probably building an agent layer when you should be building a tool layer. The LLM calling your tools is already an agent — let it do its job.

Exceptions: if your tool does something mechanical with an LLM (like structured extraction from unstructured text), that's fine. But if it's "thinking" or "deciding" — that's the calling LLM's job.

## 19. Graceful Degradation Per Data Source

If your server fetches from multiple APIs, each should degrade independently. Don't let one failed source kill the whole tool call.

```python
result = {"kind": "property_data"}

try:
    result["comps"] = fetch_comps(postcode)
except Exception:
    result["comps"] = {"error": "Comparable sales unavailable"}

try:
    result["epc"] = fetch_epc(postcode, address)
except Exception:
    result["epc"] = {"error": "EPC data unavailable"}

# Always return something useful
return result
```

The model can work with partial data. A response with "comps unavailable but here's the EPC and location data" is better than a 500 error.

## 20. Token Budget for Tool Returns

LLMs have context limits. A tool that dumps 50 comparable sales transactions at 200 tokens each burns 10K tokens of context on data the model will mostly ignore.

Rules of thumb:
- Keep tool returns under 8K tokens
- Return top 10 results, not all 50
- Exclude `raw` fields (full API responses)
- Exclude image URLs (the model can't render them)
- Summarise statistics instead of returning every data point
- Use the `exclude` parameter on Pydantic's `model_dump()` to strip verbose fields

The model needs enough data to write informed copy. It doesn't need the full API dump.

## 21. Fly.io Deployment Pattern

For Python MCP servers on Fly.io:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
EXPOSE 8080
CMD ["your-entry-point"]
```

```toml
# fly.toml
app = "your-app-name"
primary_region = "lhr"   # London for UK data APIs

[http_service]
internal_port = 8080
force_https = true
auto_stop_machines = "stop"
auto_start_machines = true
min_machines_running = 0   # Scale to zero when idle
```

Secrets via `fly secrets set KEY=value`. The `PORT` env var is set by Fly.io automatically.

`auto_stop_machines = "stop"` + `min_machines_running = 0` means you pay nothing when idle. Cold starts take 2-5 seconds — acceptable for MCP tools but not for latency-sensitive APIs.

## 22. Testing with MCP Inspector

The MCP Inspector is the quickest way to test your server without configuring a full client:

```bash
npx @modelcontextprotocol/inspector http://localhost:8080/mcp
```

This opens a browser UI where you can:
- List available tools
- Call tools with arguments
- See the raw MCP protocol messages
- View resource contents

Note: Inspector is a browser app connecting directly to your MCP endpoint, so it IS one of the cases where CORS matters. For local testing this usually isn't an issue.

## 23. The fastmcp.json Config File

For projects that will be installed by others, create a `fastmcp.json` at the project root:

```json
{
    "$schema": "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json",
    "source": {
        "path": "src/my_server/server.py",
        "entrypoint": "mcp"
    },
    "environment": {
        "dependencies": ["my-data-package>=1.0.0"]
    }
}
```

The `fastmcp install` command reads this automatically. It's the cleanest way to distribute an installable MCP server.

## 24. Don't Forget the Text Fallback (Apps-only)

Not every MCP client supports MCP Apps (the interactive UI). Many clients are text-only. Your app tools should always return useful text content alongside the structured data for the dashboard.

The model sees the text content. The dashboard sees the structured content. If you only return structured data, the model has no idea what was displayed and can't reference the results in conversation.

## 25. Pre-populate Read-Only Dashboards; Reserve CallTool Forms for Genuine Interactive Flows (Apps-only)

There are two different Prefab patterns and both are correct — for different situations. Getting this right is mostly about knowing which one you're building.

### Pattern A — Pre-populated dashboards (read-only data views)

If the data is determined entirely by the tool-call arguments and the user has no meaningful input to give, fetch the data server-side at tool-call time and return a populated view:

```python
@mcp.tool(app=True)
def comps_dashboard(postcode: str):
    data = fetch_comps(postcode)  # server-side
    view = Column(children=[
        Metric(label="Count", value=str(data["count"])),
        BarChart(data=buckets, series=[ChartSeries(data_key="count")], x_axis="range"),
    ])
    return ToolResult(
        content=f"Found {data['count']} results",
        structured_content=view,
    )
```

The user opens the tool and sees real data immediately. No button to click before anything useful renders. This is the right pattern for property reports, company profiles, comparable sales tables, metric dashboards — anywhere the argument set fully specifies what to display.

### Pattern B — Interactive forms with CallTool (genuine user-driven flows)

For **search, filters, CRUD, multi-step wizards** — anything where the user's choice determines what data to fetch — the canonical Prefab pattern is an empty form that dispatches a `CallTool` action on submit and renders the result via `SetState`. This is the reference shape from the official Prefab actions docs:

```python
with Column():
    Input(name="q", placeholder="Search...")
    Button(
        "Search",
        on_click=CallTool(
            "search_tool",
            arguments={"query": STATE.q},
            on_success=SetState("results", RESULT),
            on_error=ShowToast("{{ $error }}", variant="error"),
        ),
    )
    with ForEach(items="results", key="id"):
        Card(title=STATE.item.title)
```

This is not broken. The form starts empty because no query has been made yet; the user types, clicks the button, the server action fires, results populate via `SetState`, and the `ForEach` re-renders. It's how search boxes, filter bars, and any user-driven flow are supposed to work in Prefab.

**An earlier version of this lesson labelled all empty CallTool forms as an anti-pattern.** That was an over-generalisation from a specific debugging session where `CallTool` dispatch was misrouted on a non-`FastMCPApp` server, which made the forms *look* broken but wasn't the forms' fault. The canonical pattern is fine; the specific failure modes to actually watch for are:

- **CallTool routing requires `FastMCPApp`.** If your server isn't a `FastMCPApp` with `@app.tool()` backend tools registered, `CallTool` calls won't route. See lesson 28.
- **Missing initial state.** If the view references `STATE.xxx` but that key isn't in the `state={}` dict on the `PrefabApp`, the first render can look broken. Always initialise every state key the view reads.
- **Model fallback artifacts.** If the dashboard renders blank or frozen in claude.ai, Claude sometimes generates its own HTML/Chart.js artifact instead, burning 5-10k output tokens. Good text fallback in `ToolResult.content` mitigates this — the model has data to work with even if the widget itself fails to render.

### Rule of thumb

- **No meaningful user input?** Pattern A (pre-populate). The user opened the tool to see data; give them data.
- **User needs to search/filter/choose/submit before meaningful data exists?** Pattern B (empty form + CallTool). The form starting empty is expected, not broken.

For read-only views, pre-populating every time is still the strong default. Don't build an "enter your postcode here and click submit" form for a tool whose MCP parameter is already `postcode: str`.

## 26. Prefab Python Uses snake_case; Wire Format Is camelCase (Apps-only)

Prefab's Python models use **snake_case** field names — `data_key`, `name_key`, `x_axis`, `css_class`, `on_click`, etc. That's what every example in the official Prefab docs at `prefab.prefect.io/docs/concepts/*.md` uses, and it's the canonical Python form.

```python
# Canonical — snake_case, matches the official Prefab docs
BarChart(
    data=data,
    series=[ChartSeries(data_key="value")],
    x_axis="label",
)
```

The models have `populate_by_name=True` + `validate_by_alias=True` in their `model_config`, so **both** the snake_case field name and a camelCase alias work at construction. But the serializer is the decisive part: every Prefab component's `to_json()` method calls `model_dump(by_alias=True, exclude_none=True)` ([prefab_ui/components/base.py:354](file:///path/to/.venv/lib/python3.13/site-packages/prefab_ui/components/base.py#L354)). **On the wire, the JSON keys are always camelCase** — `dataKey`, `xAxis`, `cssClass` — regardless of which form the Python code wrote at the call site. The React renderer in claude.ai always sees camelCase.

**There are no silent failures from using snake_case.** An earlier version of this lesson claimed otherwise — that was wrong. Evidence:

1. Pydantic `model_config` explicitly sets `populate_by_name=True` and `validate_by_name=True`
2. The empirical test `PieChart(data=..., data_key="count", name_key="x")` constructs cleanly and serializes to `{"dataKey": "count", "nameKey": "x"}` on the wire
3. Every example in the official Prefab docs — `core-concepts.md`, `components.md`, `state.md`, `actions.md` — uses snake_case throughout

**Field-name traps that are NOT casing** but worth knowing:

- `Tab(title=...)` — the field is `title`, not `label`. Different name, not a casing issue.
- `ForEach(key=...)` — the state-key identifier is required. Existence issue, not casing.
- Numeric vs. string type mismatches on things like `Ring(size=...)` or `Image(height=...)` — always check the type annotation, don't guess.

**Always verify the actual field names and types** in the `prefab-ui` version your server is pinned to, before adding new components:

```bash
uv run python -c "
from prefab_ui.components.charts import BarChart, PieChart, ChartSeries
for cls in (BarChart, PieChart, ChartSeries):
    print(f'=== {cls.__name__} ===')
    for name, field in cls.model_fields.items():
        alias = field.alias or ''
        print(f'  {name:20} alias={alias!r:20} type={field.annotation}')
"
```

This prints the canonical snake_case field name, the camelCase alias (shown on the wire), and the type annotation. Run it in the server's own `.venv` so you're reading the exact `prefab-ui` version the Docker image ships.

## 27. Images Don't Work in claude.ai Prefab Views (Apps-only)

As of April 2026, images cannot be displayed inside Prefab views on claude.ai. The renderer iframe runs on `*.claudemcpcontent.com` with a strict CSP that only allows pre-approved domains (`cdn.jsdelivr.net`, `assets.claude.ai`).

**What doesn't work:**
- `Image(src="https://...")` — CSP blocks external URLs
- `Image(src="data:image/png;base64,...")` — data URIs blocked too
- Proxying images through your own domain — CSP blocks your domain
- `PrefabAppConfig(csp=ResourceCSP(resource_domains=[...]))` — declarations are not honoured by the host

**What does work:**
- BarChart, Sparkline, and other chart components (their JS loads from pre-approved `cdn.jsdelivr.net`)
- All text/layout/data components (Badge, Metric, Card, Table, etc.)

**FastMCP `ImageContent` blocks** (base64 returned as MCP content) are visible to Claude's reasoning but not rendered inline in the response ([anthropics/anthropic-sdk-python#1329](https://github.com/anthropics/anthropic-sdk-python/issues/1329)).

For now, include URLs as text so users can click through to view images externally.

## 28. FastMCPApp Is Only Needed for CallTool Routing (Apps-only)

If all your dashboards are pre-populated (`@mcp.tool(app=True)`) and don't need the UI to call backend tools via `CallTool`, you don't need `FastMCPApp`. Plain `@mcp.tool()` on the `FastMCP` instance is simpler and has fewer moving parts.

```python
# Unnecessary complexity — FastMCPApp with no CallTool usage
app = FastMCPApp("MyApp")
mcp = FastMCP("Server", providers=[app])

@app.tool(model=True)
def get_data() -> dict: ...

@app.ui()
def dashboard() -> PrefabApp: ...

# Simpler — everything on mcp directly
mcp = FastMCP("Server")

@mcp.tool()
def get_data() -> dict: ...

@mcp.tool(app=True)
def dashboard(): ...
```

Use `FastMCPApp` only when you need `@app.tool()` backend tools that the UI calls via `CallTool`, with visibility control (model vs app).

## 29. The Model Will Build Its Own Artifact If Your Widget Looks Broken

If a Prefab dashboard renders blank or unresponsive, Claude notices and generates a custom HTML/CSS/JS artifact with Chart.js, sortable tables, and colour-coded badges. This is impressive but costs 5-10k tokens of output — bad for the user's quota.

Prevention:
- Pre-populate dashboards (lesson 25) so they never render blank
- Return good text fallback in `ToolResult.content` so the model has data to work with even if the UI fails
- Test every dashboard in claude.ai before shipping to clients

## 30. Install `zstandard` or Your Proxy Client Will Silently Eat Tool Lists

If your server is itself an MCP client — i.e. it uses `create_proxy()` or `FastMCP.Client` to call upstream MCP servers — and any upstream runs on Fly.io, you **must** install the `zstandard` Python package. Without it, every upstream `tools/list` response comes back as raw zstd bytes and silently fails to parse as JSON.

Fly.io's edge proxy compresses responses with zstd regardless of whether the client advertises `Accept-Encoding: zstd`. The MCP client library's httpx layer inside `mcp.client.streamable_http` expects to receive either uncompressed JSON or a compression it can decode. Without `zstandard`, httpx can't register a zstd decoder, and the raw compressed body reaches `JSONRPCMessage.model_validate_json()` which produces:

```
ValidationError: Invalid JSON: expected value at line 1 column 1
input_value=b'(\xb5/\xfd\x00H...'
```

The leading `28 b5 2f fd` is the zstd magic number — if you see these bytes in a JSON parser error, it's always this bug.

**Worst part:** the upstream call doesn't raise. The proxy layer catches the parse error and returns an empty tool list for that upstream. Your bridge aggregates zero tools from that namespace and the client sees "no tools available" — a symptom that looks like a protocol version mismatch, an auth issue, or a routing problem, but is actually a decoder gap.

**Fix:**

```
# requirements.txt
zstandard>=0.22.0
```

No code change needed. httpx auto-registers the zstd decoder when the package is importable.

**How to spot it after the fact:** `fly logs` shows tracebacks from `mcp/client/streamable_http.py` in `_handle_json_response`, always pointing at `model_validate_json`, always with a byte-string input value starting `b'(\xb5/\xfd'`. If you see that pattern, stop debugging protocol versions and just install `zstandard`.

**Only affects servers that themselves make outbound MCP calls.** A plain FastMCP server that only serves tools (doesn't proxy) is unaffected — the response compression is handled by its ASGI server on the outbound side where the client (e.g. claude.ai) already has a full zstd decoder.

## 31. `!skills/**/*.md` Belongs in Both `.gitignore` and `.dockerignore`

If you're using `SkillsDirectoryProvider` to ship `SKILL.md` files as MCP resources, and you have a broad `*.md` exclusion in either ignore file (common — e.g. to keep `ref/` or `docs/` out of git and the Docker context), your skills are silently filtered out of the build context. The `skills/` directory gets copied, but every `SKILL.md` inside it is missing from the image. `SkillsDirectoryProvider` then reads empty directories at startup and serves no resources.

**Symptom:** `resources/list` returns an empty array (or only non-skill resources). The skill trigger descriptions never reach the LLM, so trigger-phrase activation never fires. The server looks healthy, tools work fine, but skills just… aren't there.

**Fix both files with an explicit negation:**

```gitignore
# .gitignore
*.md
!README.md
!CLAUDE.md
!skills/**/*.md
```

```dockerignore
# .dockerignore
*.md
!README.md
!CLAUDE.md
!skills/**/*.md
```

Both files need the negation independently — `.gitignore` controls what `git ls-files` sees, `.dockerignore` controls what gets into the `COPY skills/ ./skills/` layer. A file can be in git but still filtered from the Docker build, or vice versa, depending on which ignore has the negation.

**Verify after the fix:**
- `git ls-files skills/` should list every `SKILL.md`
- `docker build .` with `RUN ls skills/*/` should show the files
- After deploy, the `resources/list` JSON-RPC call should return one `skill://{name}/SKILL.md` entry per skill

**Gotcha:** `git negation` requires that the parent directory *itself* isn't excluded. If you ever write `skills/` (without the glob) as an ignore, the negation won't rescue the files inside.

## 32. `create_proxy()` Already Isolates Upstream Sessions — Stateless Is a Free Win for Aggregator Bridges

When you're considering `stateless_http=True` for a server that uses `create_proxy()` to aggregate multiple upstream MCP servers, you might worry that stateless mode will force the bridge to reinitialise its upstream sessions on every incoming request — turning each tool call into N+1 extra roundtrips.

**It doesn't.** From the FastMCP docs ([servers/providers/proxy.mdx](https://gofastmcp.com/servers/providers/proxy#session-isolation)):

> `create_proxy()` provides session isolation — each request gets its own isolated backend session (recommended).

The bridge's upstream sessions are **already** per-request, regardless of whether the bridge itself runs stateful or stateless. `stateless_http=True` only changes how the client-facing side works:

| Layer | Without `stateless_http` | With `stateless_http=True` |
|-------|--------------------------|----------------------------|
| Client → Bridge | Server-side session kept in memory per machine | Fresh transport per request, no session state |
| Bridge → Upstream (via `create_proxy`) | Fresh upstream session per incoming request | Fresh upstream session per incoming request |

For aggregator bridges there's zero perf cost to enabling stateless mode and significant wins: no session-affinity headaches, multi-machine safe, scale-to-zero compatible, no "Session not found" errors ever.

**Only exception** — if you explicitly pass an already-connected `Client` instance to `create_proxy()`, the proxy reuses that shared session:

```python
# This pattern shares a single upstream session across all requests
async with Client("backend_server.py") as connected_client:
    proxy = create_proxy(connected_client)  # shared session
```

The docs warn against this in concurrent scenarios. Unless you're deliberately doing single-threaded shared-session work, pass URLs or transport objects to `create_proxy()` instead — that gets you the per-request isolation and makes stateless mode a pure upgrade.

**Practical takeaway:** if your FastMCP server is a proxy or an aggregator, there is essentially no reason *not* to set `stateless_http=True`. Do it at the `http_app()` call in your ASGI export.

## 33. The Three-Layer Cake of Tool Response Failures (json.dumps, dict, middleware)

This lesson distils a half-day debugging session that touched three different "fixes" before finding the actual root cause. If you see this error from a strict MCP client (claude.ai through a bridge, or anything else that validates against `outputSchema`):

```
Tool {name} has an output schema but did not return structured content
```

…the cause is one of three layers, and you have to peel them in order. Don't stop at layer 1 like I did the first three times.

### Layer 1: Return type annotation

The return type annotation determines how FastMCP wraps your response on the wire.

```python
# ❌ Wrong — manual json.dumps, -> str annotation, fights the framework
@mcp.tool
async def get_judgment(uri: str) -> str:
    data = await fetch(uri)
    return json.dumps({"uri": uri, "content": data})

# ✅ Right — return the dict, let FastMCP serialise
@mcp.tool
async def get_judgment(uri: str) -> dict:
    data = await fetch(uri)
    return {"uri": uri, "content": data}
```

**Why it matters:** `-> str` tools get a generic `{"result": "string"}` wrapper schema; the framework auto-wraps the return as `{"result": "<your string>"}`. `-> dict` tools get an `{"type": "object", "additionalProperties": true}` schema and the framework emits the dict directly as `structuredContent`. They're fundamentally different wire shapes, and only the dict path matches what callers expect when they ask for "JSON output".

**This alone may not be the fix.** If you've corrected the annotation and still see the error, keep peeling.

### Layer 2: `ToolResult` for explicit control

If you need both a custom human-readable `content` block AND `structured_content`, FastMCP's `ToolResult` lets you set them independently:

```python
from fastmcp.tools.tool import ToolResult

@mcp.tool
async def get_judgment(uri: str) -> ToolResult:
    data = await fetch(uri)
    payload = {"uri": uri, "content": data}
    return ToolResult(
        content=json.dumps(payload),     # explicit text block
        structured_content=payload,      # explicit structured field
    )
```

Reach for this **only** when:
- You want to format the human content differently from the machine data (e.g. YAML for humans, dict for machines)
- You need to attach runtime `meta=` for performance metrics or debugging info
- You're transforming something through `Tool.from_tool()`

For a normal tool, just return the dict and let the framework handle both fields. Reaching for `ToolResult` when a dict would do is almost always a sign you're solving the wrong problem.

### Layer 3: Server-side response-limiting middleware (the real culprit, usually)

This is the one that ate four hours of debugging. **If you've fixed layers 1 and 2 and the error persists, you have middleware on the server that's stripping `structured_content` from oversize responses.**

FastMCP ships `fastmcp.server.middleware.response_limiting.ResponseLimitingMiddleware` which truncates tool responses that exceed `max_size` bytes. Its truncation path:

```python
# Excerpt from FastMCP source, paraphrased:
def _truncate_to_result(self, text, meta):
    return ToolResult(
        content=[TextContent(type="text", text=truncated)],
        meta=meta if meta is not None else {},
    )
```

A truncated response is a single text content block — **`structured_content` is dropped entirely**. The middleware's source comment acknowledges this trade-off:

> Having meta set ensures `to_mcp_result()` returns a CallToolResult, which **bypasses MCP SDK outputSchema validation — a truncated response is no longer valid structured output**.

That `meta={}` workaround only helps if the client uses FastMCP's in-process result path. **Wire-format clients (anything calling MCP over HTTP, including the bouch-mcp-bridge proxying to claude.ai) don't use that path** — they validate the on-wire response against the `outputSchema` advertised in `tools/list`, see no `structuredContent`, and reject the call.

The result: **a tool that returns small payloads works fine, but the same tool fails on large payloads with the structured-content error**, and you spend hours chasing return-type annotations when the real fix is one line in your gateway config.

### The fix: per-tool truncation, no server-side middleware

Don't use `ResponseLimitingMiddleware`. Don't use any middleware that mutates response shape. Truncate at the tool level instead, where the tool author knows what's safe to cut and the response stays a valid object:

```python
@mcp.tool
async def get_judgment(uri: str, max_chars: int = 50000) -> dict:
    raw = await fetch(uri)
    truncated = len(raw) > max_chars
    content = raw[:max_chars] + "\n<!-- ...truncated -->" if truncated else raw
    return {
        "uri": uri,
        "content": content,
        "truncated": truncated,
        "original_length": len(raw),
    }
```

Caller sees `truncated: true` and can re-request with a higher `max_chars` if they actually need the full thing. Schema stays valid in both cases. No middleware drops anything.

### How the debugging actually went, in case you're staring at the same error

1. **First attempt** (commit `73169e6`): Wrapped XML in `json.dumps({...})` inside a `-> str` tool. Fixed the original raw-XML parse error but hit the structured-content schema mismatch.
2. **Second attempt** (commit `ebd5caf`): Switched to `-> dict` return. Cleaner code per FastMCP docs, but the response wire format STILL had no `structuredContent` — and we couldn't figure out why.
3. **Third attempt** (commit `2f2280e`): Switched to `ToolResult(content=..., structured_content=...)` to force the field. Worked for small error responses (404s, ~100 bytes), STILL failed for large successful responses (~80k bytes of XML). That asymmetry was the key clue.
4. **Root cause found**: `ResponseLimitingMiddleware(max_size=80000)` in the gateway. SSH'd into the deployed image, read the FastMCP middleware source, found the truncation path that explicitly drops `structured_content`. The middleware was added months earlier as a defensive measure when the tool returned raw XML strings; back then the truncation behaviour was harmless.
5. **Final fix** (commit `a99f734`): Removed the middleware, reverted the tool to `-> dict` (the simplest pattern), added `max_chars` parameter so the tool truncates itself before responses get large enough to need framework-level limits.

The actual fix was 9 lines of net code change. The lesson took half a day.

### Reference commits (uk-legal-mcp)

- `73169e6` — Layer 1 attempt (json.dumps wrap) — incomplete
- `ebd5caf` — Layer 1 attempt (-> dict return) — exposed layer 3
- `2f2280e` — Layer 2 attempt (ToolResult) — confirmed layer 3 was the culprit
- `a99f734` — **The real fix**: drop ResponseLimitingMiddleware, simplify tool, add max_chars param

### TL;DR

- **Always start with `-> dict`** for structured returns (or `-> str` for plain text). Don't `json.dumps`. Don't reach for `ToolResult` unless you genuinely need explicit content/meta control.
- **If you still see the error after fixing the return type, look for response-mutating middleware.** Specifically `ResponseLimitingMiddleware` or anything custom that calls `result.content = ...`. Remove it.
- **Truncate at the tool level** with a `max_chars` parameter, not at the server level.
- **The size at which it fails is meaningful** — if small responses work but large ones don't, it's almost certainly middleware truncation.

## 34. ResourcesAsTools Is a Trap for Tool-Only Clients

`ResourcesAsTools` is a FastMCP transform that generates two synthetic tools — `list_resources` and `read_resource` — so tool-only clients can access resources without native resource support.

The trap: `read_resource` always double-encodes. Resources must return `str` (that's the MCP resource contract). FastMCP wraps any `str` return from a tool as `{"result": "<string>"}` in `structuredContent`. So a client calling `read_resource` gets `{"result": "{\"company_number\": ...}"}` — a JSON string inside a JSON object — and has to parse twice. Most clients don't. ChatGPT shows "No tool response" and crashes mid-session.

```python
# The transform looks harmless
mcp.add_transform(ResourcesAsTools(mcp))

# But read_resource always produces:
# structuredContent: {"result": "{\"company_number\": \"03782379\", ...}"}
# instead of:
# structuredContent: {"company_number": "03782379", ...}
```

**There is no fix for this within ResourcesAsTools.** The double-encoding is structural: resources return `str`, FastMCP wraps `str` tool returns, the result is always double-encoded.

**Remove the transform.** Add named companion tools instead (lesson 35). Keep resources for clients that support them natively.

You cannot detect this problem from the client side — tool-only clients don't advertise `ClientCapabilities.resources`. You have to know that `read_resource` is broken for them before they tell you.

**How to spot it in MCP Inspector:** call `read_resource` via the Tools tab. If `structuredContent` is `{"result": "..."}` (a string value, not an object), you have the double-encoding problem.

## 35. Dual-Surface Pattern: Resources + Named Companion Tools

The MCP client ecosystem is split:

- ~46 of 101 listed clients support Resources natively (Claude Desktop, Cursor, Cline, Claude Code)
- The rest are tools-only (ChatGPT, many custom integrations)
- `ClientCapabilities` has no `resources` field — the server cannot detect which kind of client is connected

The dual-surface pattern handles both without giving up resources:

1. **Keep `@mcp.resource` handlers** — they serve protocol-compliant clients cleanly and are the right primitive for fetch-by-URI operations
2. **Add named `@mcp.tool` companions** that call the same underlying `_fetch_*` helper and return the Pydantic model directly (not `model_dump_json()`)

```python
# Shared fetch helper — called by both tool and resource
async def _fetch_company_profile(company_number: str) -> CompanyProfile:
    ...

# Tool surface — for ChatGPT and tool-only clients
@mcp.tool(name="company_profile")
async def company_profile(company_number: str) -> CompanyProfile:
    return await _fetch_company_profile(company_number)

# Resource surface — for Claude Desktop, Cursor, etc.
@mcp.resource("company://{company_number}/profile")
async def company_profile_resource(company_number: str) -> str:
    result = await _fetch_company_profile(company_number)
    return result.model_dump_json()   # str is correct here — resources must return str
```

**The cardinal rule that makes this work:**
- Tools: return Pydantic model or dict — FastMCP auto-serialises to clean `structuredContent`
- Resources: return `str` — call `model_dump_json()` here and **only** here

Never call `model_dump_json()` from a tool return path. That turns it into a `str` return, triggers the `{"result": "..."}` wrapper, and you're back to double-encoding.

## 36. ChatGPT Caches the Tool List at the Connector Level, Not Per-Conversation

ChatGPT fetches `tools/list` once when a server is connected — at the integration/connector level — and caches it. Starting a new conversation does **not** re-fetch the tool list. Refreshing the page does not re-fetch it.

This means: after you deploy a new version that adds, removes, or renames tools, ChatGPT continues calling the old tool set until you force a reconnect.

**How to force a re-fetch:**
1. ChatGPT Settings → Apps (formerly Connectors)
2. Remove the MCP server
3. Re-add it
4. Start a new conversation

**How to diagnose a stale session from the server side:**
```bash
fly logs --no-tail | grep "TOOL\]"
```
If you see `[TOOL] list_resources()` or `[TOOL] read_resource(...)` after removing `ResourcesAsTools`, ChatGPT is calling tools from the old cached list. The session is stale regardless of what the client UI shows.

**The UI label is unreliable.** ChatGPT's tool call list labels tool calls by what it *thinks* it's doing — "company_profile" in the UI may actually be `read_resource(uri='company://03782379')` on the wire. Always check `fly logs` for the actual tool name being called.

## 37. ChatGPT Deep Research and Company Knowledge Require Tools Named `search` and `fetch`

ChatGPT's deep research and company knowledge features only work with tools named exactly `search` and `fetch`. Without them, ChatGPT falls back to web search regardless of what other MCP tools are present and working correctly.

FastMCP has a canonical pattern for this (confirmed via FastMCP docs):

```python
@mcp.tool()
async def search(query: str) -> dict:
    """Search for records. Returns {"ids": [...]} of matching record IDs."""
    ...
    return {"ids": ["company:03782379", "notice:2948343", ...]}

@mcp.tool()
async def fetch(id: str) -> dict:
    """Fetch a full record by ID returned from search."""
    ...
    return {"id": id, "title": "...", "content": "...", "metadata": {...}}
```

**`search` returns `{"ids": [...]}` — just IDs, not full records.** The LLM calls `fetch` on the IDs it cares about. This gives the LLM control over which results to expand.

**`fetch` routes by ID prefix.** Use a prefixed ID scheme so a single `fetch` tool can cover multiple data sources:

```
company:{number}          → Companies House profile
charity:{number}          → Charity Commission profile
disqualification:{id}     → Disqualified director record
notice:{id}               → Gazette notice full text
```

**`search` should fan out across all your data sources in parallel** using `asyncio.gather(*tasks, return_exceptions=True)`. Drop any source that raises. Merge results into one flat ID list.

**Both tools return dicts** — FastMCP serialises them cleanly to `structuredContent`. Don't return `str` or call `json.dumps`.

These tools are additive — they sit alongside your existing named tools. Claude and other clients continue using the named tools (`company_search`, `company_profile`, etc.); ChatGPT deep research uses `search` and `fetch`.

## 38. ResponseCachingMiddleware Is Dangerous During Active Development

`ResponseCachingMiddleware` caches tool responses keyed by tool name + arguments, in memory, for the lifetime of the server process.

The danger: **the cache doesn't know your code changed**. A deploy restarts the process, so the cache is cold after a fresh deploy — but if Fly.io reuses a warm machine (which it often does for single-machine deployments), the old cached responses survive the deploy.

During active refactoring this causes phantom "No tool response" failures on tools you've just fixed: the cache returns the pre-fix response, the client gets broken output, and `fly logs` shows the tool completing in 0.0s (a cache hit) — which looks healthy.

**Pattern that burns you:**
1. Tool had a bug (e.g. returning `str` instead of Pydantic)
2. Client calls the tool — broken response gets cached
3. You fix the bug and deploy
4. Client calls the tool again — cache returns the old broken response
5. Tool looks broken. It isn't. The cache is lying.

**During development: remove the middleware.** Add it back once the API surface has stabilised and you're confident the responses are correct.

```python
# Development
mcp = FastMCP("my_server", middleware=[ToolLogger()])

# Production (once stable)
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
mcp = FastMCP("my_server", middleware=[ToolLogger(), ResponseCachingMiddleware()])
```

**How to spot it:** a tool completes in 0.0s (cache hit speed) but returns wrong output. `curl` the MCP endpoint directly to bypass the cache and see the live response.

---

## Quick Reference: What Goes Where

| Setting | Location | Example |
|---------|----------|---------|
| Server name | `FastMCP()` constructor | `FastMCP("My Server")` |
| Instructions | `FastMCP()` constructor | `instructions="..."` |
| Auth | `FastMCP()` constructor | `auth=BearerTokenAuth(...)` |
| Transport | `mcp.run()` | `transport="http"` |
| Stateless mode | `mcp.run()` or `mcp.http_app()` | `stateless_http=True` |
| Host/port | `mcp.run()` | `host="0.0.0.0", port=8080` |
| MCP path | `mcp.run()` or `mcp.http_app()` | `path="/mcp"` |
| Lifespan context | `ctx.lifespan_context` | `ctx.lifespan_context["client"]` |
| Context injection | Tool param type hint | `ctx: Context` or `ctx: Context = CurrentContext()` |
| Client config (Claude Code) | `.mcp.json` | `"type": "http"` for remote servers |
| Tool UI link (Apps) | `@mcp.tool()` | `app=True` or `app=PrefabAppConfig(...)` |
| CSP (Apps) | `PrefabAppConfig` | `csp=ResourceCSP(resource_domains=[...])` (not honoured by claude.ai) |
| Pre-populated UI | `@mcp.tool(app=True)` | `return ToolResult(content=text, structured_content=view)` |
| CORS | `mcp.http_app()` | `middleware=[Middleware(CORSMiddleware, ...)]` |
| Custom routes | `@mcp.custom_route()` | `@mcp.custom_route("/health")` |
