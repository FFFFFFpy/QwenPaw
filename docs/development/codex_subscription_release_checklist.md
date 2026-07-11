# OpenAI Codex subscription release checklist

## Delivered architecture

`CodexSubscriptionProvider` and its direct `ChatModelBase` adapter retain the
QwenPaw agent loop, memory, tool execution, permissions, and cancellation.
One process-wide runtime communicates with the official App Server over JSONL
stdio. Authentication, account data, the dynamic model catalog, and usage
windows remain owned by the App Server.

The implementation includes structured schema capability detection, a concurrent
bidirectional RPC client, browser and device-code login, model and rate-limit
mapping, text/reasoning/image turns, isolated ephemeral workspaces, dynamic
tool round trips, per-thread routing, and an explicit cloud-subscription UI.

## Automated test matrix

| Area | Result |
|---|---|
| Codex provider/runtime/RPC/auth/chat/tool/security tests | Covered by the dedicated hardening workflow on Linux, macOS, and Windows with the repository fake App Server |
| Complete subscription flow | ProviderManager lookup through login, models, usage, text, tools, interrupt, and logout passed |
| Backend full suite | `codex-provider-hardening.yml` runs unit and contract tests in separate processes to prevent environment leakage between tiers |
| Frontend suite | The workflow runs Vitest and uploads JUnit output |
| Type and style checks | Focused mypy, Black, Flake8; full frontend typecheck/format plus Codex-surface ESLint |
| Production console build | The workflow runs the production TypeScript/Vite build |
| Secret scan | Gitleaks blocks detected credentials or private keys |
| Real App Server | Workflow-dispatch only; requires `run_real_smoke=true` |

Unit coverage includes malformed and oversized JSON, out-of-order responses,
EOF/crash propagation, stderr backpressure, request timeout/cancellation,
unknown server requests, authentication expiry/failure, stale catalogs,
built-in side-effect blocking, cross-thread tool rejection, tool denial and
timeout, temporary-directory cleanup, and Windows process cleanup behavior.

## Platform status

| Platform | Automated coverage | Manual account matrix |
|---|---|---|
| macOS | Fake process/runtime, paths, streaming, tools, cancellation | Pending release QA; no live account read was used for final verification |
| Linux | Fake server advertises and exercises the Unix/Linux protocol path | Pending release QA |
| Windows | Discovery constraints and CTRL_BREAK/terminate cleanup branch | Pending release QA |

The cross-platform live-account matrix must be completed on release machines.
It is not safe to claim that macOS execution substitutes for Windows or Linux,
and automated tests deliberately do not depend on a real ChatGPT account.

## Security result

- No Codex access, refresh, or ID token is accepted by settings or persisted.
- Production integration code does not read Codex authentication files.
- The binary is resolved to an absolute executable and launched without a
  shell; QwenPaw never downloads or replaces it.
- Raw stderr, prompts, tool arguments, authorization query strings, and full
  email addresses are excluded from logs.
- Built-in command, file-change, MCP, and web side effects fail the turn.
- Dynamic tools are capability-gated and bound to thread, turn, call, and
  registered tool name; unsupported tools are never silently discarded.
- Provider listing does not start an account read. Account state is checked
  on an explicit UI action and cached briefly to prevent repeated OS prompts.
- Provider listing and `GET /runtime` are read-only and do not start Codex.
- Cancellation waits for a confirmed interrupt; cleanup failure blocks a new
  turn until explicit retry or Runtime restart.
- Full payload size includes text, images, tools, and JSON overhead; size-error
  details never contain request content.

## Known risks and limitations

- Dynamic tools use an experimental App Server surface and may require a
  compatibility update when the upstream schema changes.
- Login methods can be disabled by ChatGPT workspace policy.
- The official App Server controls platform credential storage and may show a
  Keychain or credential-manager prompt when the user explicitly checks an
  account or starts a model operation.
- The release remains gated on the Windows, macOS, and Linux manual matrix.
- A GitHub run cannot be claimed from a local commit. The dedicated workflow
  must be pushed and green before checking the GitHub Actions acceptance item.

## CI and opt-in smoke

`.github/workflows/codex-provider-hardening.yml` runs backend full/focused
tests, frontend tests, typecheck/lint/build, secret scan, and uploads a summary
artifact. The real-binary job is off by default. Release operators may enable
it from workflow dispatch, or run locally only with explicit consent:

```bash
QWENPAW_RUN_CODEX_SMOKE=1 \
  pytest tests/integration/test_codex_app_server_smoke.py -v
```

The smoke validates binary/schema/initialize/unknown-request/shutdown and, when
a ChatGPT account is connected, `account/read` plus `model/list`. Do not enable
it on a machine where platform credential access has not been authorized.

## Rollback

1. Set `QWENPAW_CODEX_SUBSCRIPTION_ENABLED=false` and restart QwenPaw.
2. If removing the feature in code, remove only the built-in provider
   registration and dedicated router/UI entry.
3. Leave the non-secret `codex_subscription/settings.json` in place or remove
   it independently.
4. Do not read, migrate, delete, or otherwise touch Codex authentication
   storage. Other providers and OpenRouter OAuth remain unchanged.
