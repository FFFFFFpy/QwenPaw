# Codex App Server protocol contract

This document records the protocol surface used by QwenPaw's
`openai-codex` cloud-subscription provider. The generated upstream schema is
the source of truth; this file is a deliberately small compatibility contract.

## Observed baseline

- Codex CLI: `codex-cli 0.144.0-alpha.4`
- Binary: `/Applications/ChatGPT.app/Contents/Resources/codex`
- Transport: stdio JSONL, one JSON object per line, JSON-RPC 2.0 semantics with
  the `jsonrpc` member omitted
- Stable schema SHA-256:
  `85ea836927d6cfdd3c68a9bda17dba48d2573bbc282ab2d5775a5005e40bc9c3`
- Experimental v2 schema SHA-256:
  `f54352a19bec547cede4886b9b3f2ec1995ef5e4815efe435f9ed0d4b8a739ba`

Regenerate these facts with:

```bash
codex app-server generate-ts --experimental --out /tmp/codex-schema/ts
codex app-server generate-json-schema --experimental --out /tmp/codex-schema/json
```

Do not commit the full generated schema. It is large and changes with the
locally installed Codex build. Update the fingerprint and the focused fixtures
when a schema change affects this contract.

## Initialization

Every connection performs exactly one handshake before any other request:

```json
{"id":1,"method":"initialize","params":{"clientInfo":{"name":"qwenpaw","title":"QwenPaw","version":"<qwenpaw-version>"},"capabilities":{"experimentalApi":true,"requestAttestation":false}}}
{"method":"initialized","params":{}}
```

Experimental API opt-in is required for `thread/start.dynamicTools`. QwenPaw
does not opt into attestation or MCP elicitation.

## Capability matrix

| Capability | Status | Contract evidence |
|---|---|---|
| Browser ChatGPT login | supported | `account/login/start` with `type=chatgpt`; response has `loginId`, `authUrl` |
| Device-code login | supported | `type=chatgptDeviceCode`; response has `verificationUrl`, `userCode` |
| Account status/logout | supported | `account/read`, `account/logout`, `account/updated` |
| Model discovery | supported | paginated `model/list`; model has effort and input modality fields |
| Rate limits | supported | `account/rateLimits/read`; primary, secondary, credits, reset timestamps |
| Text streaming | supported | `item/agentMessage/delta` scoped by thread and turn |
| Reasoning streaming | supported | summary and raw reasoning delta notifications |
| Image input | supported when model advertises it | turn input `{type: image, url: ...}`; gate by `inputModalities` |
| Turn interruption | supported | `turn/interrupt` requires `threadId` and `turnId` |
| Event unsubscription | supported | `thread/unsubscribe` |
| Dynamic tools | experimental | `thread/start.dynamicTools`; server request `item/tool/call` |
| Disable built-in side effects | supported by containment | `approvalPolicy=never`, `sandbox=read-only`, isolated cwd; all command/file/permission requests are rejected |
| Thread archive | supported | `thread/archive`; QwenPaw normally uses unsubscribe for ephemeral cycles |

The runtime detects capabilities from the generated/observed method surface,
not from a version-number comparison. Missing optional methods degrade
explicitly. Missing initialize, account, model, thread, or turn primitives make
the runtime incompatible.

## Focused message contract

- Account reads use `account/read` with `{"refreshToken": false}`. QwenPaw
  exposes only connection status, masked email, plan, and auth kind.
- QwenPaw initiates only `chatgpt` and `chatgptDeviceCode` login types. It
  never sends API keys or externally managed ChatGPT tokens.
- `model/list` is paginated. Hidden entries are excluded. Model IDs are never
  supplied from a static fallback table.
- A generation creates an ephemeral thread with an isolated cwd,
  `approvalPolicy=never`, and `sandbox=read-only`, then calls `turn/start`.
- Text and reasoning notifications must match the active thread and turn.
- `turn/completed.turn.status` is one of `completed`, `interrupted`, `failed`,
  or `inProgress`; a failed turn carries a structured error.
- A dynamic tool call contains `threadId`, `turnId`, `callId`, `tool`, and
  JSON `arguments`. Its response is `{contentItems, success}`.
- Unknown notifications are ignored after a debug log. Unknown server requests
  receive JSON-RPC method-not-found immediately.
- `item/commandExecution/requestApproval`,
  `item/fileChange/requestApproval`, permission requests, legacy command
  approvals, and patch approvals are always rejected.

## Security boundary

The probe and provider never read or parse `~/.codex/auth.json`. Authentication
storage and token refresh remain exclusively owned by the official App Server.
No access token, refresh token, session cookie, authorization URL query string,
full user prompt, or tool argument is logged or persisted by QwenPaw.

