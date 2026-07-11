# OpenAI Codex subscription security boundary

QwenPaw treats the official Codex App Server as a model and authentication
transport, not as the agent permission authority. QwenPaw owns the agent loop,
tool policy, and all side effects.

## Primary boundary

Before starting the App Server, QwenPaw parses its generated JSON Schema. A
compatible `thread/start.sandbox` must structurally support a `readOnly` policy
with `networkAccess=false` and `access.type=restricted`, whose
`readableRoots` is an array of paths. Each turn receives a new temporary cwd,
and that cwd is the sole readable root. Missing either restricted reads or
network disable produces `CODEX_SANDBOX_UNSUPPORTED`; no real turn starts.

Developer instructions, approval-request denial handlers, and blocked-event
checks are secondary fuses. They do not replace the sandbox boundary.

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
reads only the in-memory account cache and does not start the Runtime. Explicit
account checks, login completion, model refresh, and model calls may ask the
official App Server to read its own account state.

Complete thread/turn payloads are measured with the same compact UTF-8 JSON
encoder as the RPC transport. The budget includes history, Base64 image URLs,
dynamic tool schemas, and JSON overhead. Size errors expose only byte counts,
the configured limit, and attachment count.

Logs omit prompts, tool arguments, raw stderr, authorization URLs, tokens, and
full email addresses. Error details must remain scrubbed and content-free.

## Verification

The default suite uses only the repository fake App Server. A real-binary
smoke exists behind `QWENPAW_RUN_CODEX_SMOKE=1`; it is never run implicitly.
Enabling it may cause the official App Server to access its platform credential
store during `account/read`. Release operators must opt in knowingly and record
the platform result.
