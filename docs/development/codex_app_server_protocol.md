# Codex App Server protocol contract

This document records the protocol surface used by QwenPaw's
`openai-codex` cloud-subscription provider. The generated upstream schema is
the source of truth; this file is a deliberately small compatibility contract.

## Observed schema snapshot

- Codex CLI observed during schema-only review: `codex-cli 0.144.0-alpha.4`
- Transport: stdio JSONL, one JSON object per line, JSON-RPC 2.0 semantics with
  the `jsonrpc` member omitted
- Stable schema SHA-256:
  `85ea836927d6cfdd3c68a9bda17dba48d2573bbc282ab2d5775a5005e40bc9c3`
- Experimental v2 schema SHA-256:
  `f54352a19bec547cede4886b9b3f2ec1995ef5e4815efe435f9ed0d4b8a739ba`

That observed schema did not expose the restricted readable-root shape required
by QwenPaw and is therefore incompatible for chat. A fingerprint records
identity, not compatibility. Regenerate schema evidence with:

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
| Restricted file reads and disabled network | required | structured `thread/start.sandbox` must bind readable roots to the isolated cwd and set `networkAccess=false`; string sandbox modes are rejected as incompatible |
| Built-in side-effect requests | explicitly rejected | command/file/patch/permission/MCP approval methods have registered denial handlers |
| Thread archive | supported | `thread/archive`; QwenPaw normally uses unsubscribe for ephemeral cycles |

The runtime indexes request, response, server-request, client-notification, and
server-notification schemas. Its public catalog exposes `has_method`,
`request_schema`, `response_schema`, `notification_schema`,
`server_request_schema`, `supports_field`, and `supports_enum`. Field paths and
enum variants are resolved structurally; security capability detection never
uses description text, substring occurrence, or a version threshold.

Initialization, account read/login/logout, model listing, thread start and
unsubscribe, turn start and interrupt, agent-message deltas, and turn
completion are required. Missing any of these, restricted readable roots, or
network disable makes the Runtime incompatible before App Server startup or a
real turn.

## Focused message contract

- Account reads use `account/read` with `{"refreshToken": false}`. QwenPaw
  exposes only connection status, masked email, plan, and auth kind.
- QwenPaw initiates only `chatgpt` and `chatgptDeviceCode` login types. It
  never sends API keys or externally managed ChatGPT tokens.
- `model/list` is paginated. Hidden entries are excluded. Model IDs are never
  supplied from a static fallback table.
- A generation creates an ephemeral thread with an isolated cwd and a
  structured read-only sandbox whose only readable root is that cwd and whose
  network access is disabled, then calls `turn/start`.
- Text and reasoning notifications must match the active thread and turn.
- `turn/completed.turn.status` is one of `completed`, `interrupted`, `failed`,
  or `inProgress`; a failed turn carries a structured error.
- A dynamic tool call schema must contain `threadId`, `turnId`, `callId`,
  `tool`, and JSON `arguments`; its response schema must contain
  `contentItems` and `success`. `namespace` is accepted when present and may be
  a non-null string.
- Unknown notifications are ignored after a debug log. Unknown server requests
  receive JSON-RPC method-not-found immediately.
- `item/commandExecution/requestApproval`,
  `item/fileChange/requestApproval`, permission/MCP requests, legacy command
  approvals, and patch approvals have explicit rejection handlers.

## Security boundary

The structured sandbox is the primary file and network boundary. Detection of
unexpected `item/started` command, file, web, or MCP events remains a secondary
fuse that interrupts a protocol-violating turn; it is not treated as the
permission system. Developer instructions are defense in depth only.

The probe and provider never read or parse `~/.codex/auth.json`. Authentication
storage and token refresh remain exclusively owned by the official App Server.
No access token, refresh token, session cookie, authorization URL query string,
full user prompt, or tool argument is logged or persisted by QwenPaw.

See [the dedicated security boundary](./codex_subscription_security.md) for
cleanup, payload-budget, credential, and verification requirements.
