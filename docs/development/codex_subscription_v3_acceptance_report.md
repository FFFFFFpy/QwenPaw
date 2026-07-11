# ChatGPT subscription direct-access v3 acceptance report

Date: 2026-07-11 (Asia/Tokyo)

## Result

All v3 P0/P1 implementation gates and the required live-account smoke matrix
passed on the feature branch. QwenPaw remains the only agent loop: the
subscription provider is a `ChatModelBase` transport, QwenPaw executes tool
calls, and raster generation is a QwenPaw `image_generate` tool. No App Server
inference, Codex process, thread/start or turn/start route is present.

The integration targets the non-public
`chatgpt.com/backend-api/codex/responses` compatibility route. This is not the
OpenAI Platform Responses API and the upstream contract may change.

## Live-account smoke results

Only timings and non-sensitive output metadata were retained.

| Scenario | Result | TTFT / total |
| --- | --- | --- |
| Luna Low ordinary answer | PONG | 1.799 s / 1.905 s |
| Same-session follow-up | Expected follow-up answer | 1.277 s / 1.378 s |
| HintBlock | HINT_OK | 6.553 s / 7.717 s |
| One read-only tool | Correct tool call | 6.153 s / 6.155 s |
| Two tools | Both executed across ReAct turns | 3.288 s / 3.861 s |
| Image input | Correctly identified blue | 4.476 s / 4.479 s |
| Structured output | Valid `{\"ok\": true}` | 1.423 s / 1.636 s |
| Manual cancellation | Terminal `interrupted` after first delta | Passed |
| Forced OAuth refresh | Access token rotated | 0.679 s |
| Normal draw request | Luna selected `image_generate` | 2.293 s / 6.586 s |
| Normal draw request | Terra selected `image_generate` | 5.472 s / 8.854 s |
| Normal draw request | Sol selected `image_generate` | 4.027 s / 6.511 s |
| Explicit SVG request | SVG text response; no image tool | 5.428 s / 6.400 s |

Real raster generation produced an 836,796-byte `image/png` in 47.873 s.
Reference editing of that session resource produced a 1,107,311-byte
`image/png` in 31.881 s. Both were decoded, validated by Pillow and stored in
the session attachment pipeline; Base64 was not returned in the tool result or
conversation history.

## Automated verification

- Runtime, OAuth, provider, catalog, API, image tool and regression selection:
  748 passed.
- Final cancellation, mapper, image service and real-HTTP fake-server
  selection: 17 passed.
- Backend P0/P1 gate: 440 passed and 4 skipped in the initial run; the only
  failure was an unrelated plugin dependency-install read timeout, and that
  exact test passed on immediate isolated rerun (441/441 required tests passed
  across the gate and rerun).
- Complete console suite: 123 test files and 1,117 tests passed.
- Console model-management selection: 6 passed, followed by TypeScript
  checking, targeted ESLint and a production build.
- Python formatting, Flake8 on changed files, whitespace checks and forbidden
  architecture/text scans passed.

Repository-wide ESLint reports 241 pre-existing baseline errors outside the
new model-management files. No new-file ESLint error is present.

## Implementation commits

- `52e78dbe` — runtime correctness and compatibility hardening
- `29ff1ecd` — v3 per-model settings and bundled catalog
- `60511d1e` — model-management UI
- `a312ea2f` — real GPT Image 2 generation and attachment delivery
- Final smoke, compatibility fix and documentation — this report's commit

## Compatibility notes and rollback

Responses Lite disables parallel tool calls, so two-tool live validation uses
normal sequential ReAct turns. GPT Image 2 must use the full compatibility
response mode; applying the Lite header causes the upstream image request to
be rejected. The transport now selects the correct mode per request.

Set `QWENPAW_OPENAI_CODEX_DIRECT_ENABLED=false` to pause the provider without
erasing credentials or settings. There is no fallback to App Server, API keys
or another model.
