# OpenAI Codex subscription security boundary

QwenPaw treats the official Codex App Server as a model and authentication
transport, not as the agent permission authority. QwenPaw owns the agent loop,
tool policy, and all side effects.

## Primary boundary

Before starting the App Server, QwenPaw parses its generated JSON Schema.
`thread/start.sandbox` must accept the official `read-only` mode and
`turn/start.sandboxPolicy` must accept `{type: readOnly,
networkAccess: false}`. QwenPaw does not invent a restricted-readable-roots
field when the installed schema does not expose one.

The primary permission boundary is a thread-local config that disables Shell,
Web Search, MCP, Apps, Plugins, Browser Use, Computer Use, image generation,
and sub-agents. An explicit empty `thread/start.environments` selection removes
environment-backed Shell, patch, image-view, and permission tools. Only
QwenPaw Dynamic Tools and non-side-effect protocol utilities remain
model-visible. Before thread creation QwenPaw asks the App Server only for MCP
server names, then supplies a per-name `enabled=false` override; an empty MCP
table alone would not erase lower-precedence user configuration. A schema
fingerprint must be explicitly recorded after an account-backed isolation
probe before real chat is allowed. The ordinary settings API exposes recorded
fingerprints as read-only state and cannot add or replace them.

Developer instructions, approval-request denial handlers, interruption, and
blocked-event checks are protocol-violation circuit breakers. They do not
replace tool isolation.

## Cancellation boundary

Cancellation and tool timeout start a Runtime-owned cleanup task. Cleanup
waits for `turn/interrupt`, removes tool routing and notification handlers,
unsubscribes the thread, and deletes the temporary cwd. A new turn waits on the
old cleanup barrier. Failure to confirm interruption is retained as
`CODEX_INTERRUPT_FAILED` and blocks another turn until cleanup is explicitly
retried or the Runtime is restarted.

## Data and credential handling

QwenPaw never reads `~/.codex/auth.json`, copies tokens, accepts externally
managed ChatGPT tokens, or writes subscription credentials. Provider listing
reads only the in-memory last-known account snapshot and does not start the
Runtime. Explicit account checks, login completion, model refresh, and model
calls may ask the official App Server to read its own account state.

Each `thread/start` and `turn/start` request is measured separately with the
same compact UTF-8 JSON encoder as the RPC transport. The budget includes the
request id placeholder, method, parameters, history, Base64 image URLs,
dynamic tool schemas, and JSON overhead. Size errors expose only the method,
byte counts, configured limit, and attachment count.

Logs omit prompts, tool arguments, raw stderr, authorization URLs, tokens, and
full email addresses. Error details must remain scrubbed and content-free.

## Verification

The default suite uses only the repository fake App Server. A no-account real
binary smoke may generate schema, initialize, exercise unknown RPC handling,
and stop the Runtime. It must not call `account/read`.

Every pytest process disables QwenPaw keyring access and redirects its secret
directory before the first `qwenpaw` import. The Codex tool-isolation probe
does the same for QwenPaw's own secret store. This keeps automated tests and
schema-only probes from reading or prompting for a developer's OS keychain.

Account, model, text-turn, Dynamic Tool, cancellation, usage-limit, and tool
isolation probes are a separate explicit release gate. They may cause the
official App Server to access its credential store and must never run as an
ordinary CI smoke.

Only a successful account-backed `probe_codex_tool_isolation.py` run with an
explicit `--record-settings` path may append the current schema fingerprint.
The script records nothing when any built-in side-effect item is observed, the
Dynamic Tool is not called, or the probe otherwise fails.
