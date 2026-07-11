# ChatGPT/Codex subscription compatibility access

QwenPaw can use an eligible ChatGPT/Codex subscription through a direct
compatibility transport. This calls `chatgpt.com/backend-api/codex/responses`;
it is not the OpenAI Platform Responses API or a public, long-term stable SDK.
The upstream route may change and requires ongoing compatibility tests.

QwenPaw remains the sole agent runtime. AgentScope ReAct, permissions,
Governor, middleware, context management, stop handling, skills and tool
execution are unchanged. The provider handles only OAuth, refresh, HTTP/SSE,
message mapping, model capability metadata and provider errors.

## Sign in

Open **Settings → Models → OpenAI ChatGPT/Codex 订阅** and choose **登录
ChatGPT**. QwenPaw uses Authorization Code with PKCE S256 and a one-time,
15-minute state value.

On the same machine, the browser returns to
`http://localhost:1455/auth/callback` and QwenPaw completes login. If QwenPaw
runs on a Mac but the browser is on Windows, the localhost page may not open.
Copy the full callback URL from the address bar and paste it into QwenPaw.

Tokens are encrypted by QwenPaw's Secret Store, scoped to provider
`openai-codex` and a local account ID, and never returned by the API. QwenPaw
does not read `~/.codex` or copy credentials from another application.

## Models and context

The initial subscription catalog contains `gpt-5.6-sol`, `gpt-5.6-terra`, and
`gpt-5.6-luna`. Availability is determined by the account when used; QwenPaw
never silently changes models. The working context is 262,144 tokens, with
90% compaction at 235,930 tokens. Maximum output capability is displayed as
128K and is read-only. Reasoning options are clipped per model.

## Troubleshooting and rollback

- 401 or `invalid_grant`: sign in again.
- 403/404: the subscription cannot use the selected model.
- 429: subscription quota or rate limit is active.
- Compatibility error: the upstream private route changed; update QwenPaw.
- Network error: verify access to `auth.openai.com` and `chatgpt.com`.

Set `QWENPAW_OPENAI_CODEX_DIRECT_ENABLED=false` to pause this provider. This
does not switch to App Server, an API key, or another model, and does not erase
credentials or saved provider configuration.
