# OpenAI Codex cloud subscription

QwenPaw can use the models included with an eligible ChatGPT subscription
through the official local Codex App Server. QwenPaw remains responsible for
the agent loop, memory, channels, schedules, permissions, and tool execution.
The App Server owns ChatGPT authentication, model discovery, usage windows,
and model streaming.

## Install Codex

Install the official Codex CLI for your operating system and confirm that the
`codex` executable is on `PATH`:

```bash
codex --version
codex app-server --help
```

If QwenPaw cannot find it, open **Models → OpenAI Codex → Settings** and select
the executable by its absolute path. QwenPaw never downloads or replaces the
binary automatically.

## Connect ChatGPT

1. Open **Settings → Models**.
2. Find the **OpenAI Codex** cloud-subscription card.
3. Select **Check account** to reuse an existing Codex login, or select
   **Connect ChatGPT** to start a new login.
4. Complete the official browser sign-in page.

The Codex App Server hosts the local OAuth callback. QwenPaw does not exchange
the authorization code and does not receive or store OAuth tokens.

On macOS, the official App Server may ask permission to read its own login
record from Keychain. Merely listing QwenPaw providers does not perform an
account read or start the Runtime; the card initially shows an unknown account
state, never **Live**. Credential access happens only after an explicit account
check, login completion, model refresh, or model call. Denying a platform
credential prompt results in a visible account/model error; QwenPaw does not
require approval merely to list providers.

For a headless machine or a blocked local callback, select **Use device code**.
Open the displayed verification URL, enter the one-time code, and leave the
QwenPaw dialog open until it reports completion. Device-code availability can
depend on the ChatGPT account or workspace policy.

## Models and reasoning

The model list comes from the connected account's `model/list` response. It is
not a static list maintained by QwenPaw. Select **Refresh** after changing
accounts or plans, then choose one of the discovered models as the active LLM.
Image support and reasoning-effort options are taken from the same catalog.
The effective reasoning effort is selected in this order: per-call override,
saved model setting, current catalog default, then no explicit value. Values
outside the model's advertised options are rejected before `thread/start`.

If a previously selected model disappears, QwenPaw reports it as unavailable
instead of silently substituting another model.

## Usage windows

The card displays the App Server's subscription usage percentage and reset
time. These values are account usage windows, not token-balance estimates.
QwenPaw does not derive subscription quota from chat token counts.

## QwenPaw tools

When the installed App Server advertises experimental dynamic-tool support,
Codex can request a QwenPaw tool. QwenPaw's permission system decides whether
the tool is allowed, executes it outside the App Server, and returns the result
to the same Codex turn. If dynamic tools are unavailable or disabled, a chat
that supplies tools fails clearly; tools are never silently discarded.

Codex built-in command, file-change, web, MCP, and other side-effect paths are
not authorized. Compatible App Server versions must support a structured
read-only policy that restricts readable roots to QwenPaw's isolated temporary
directory and disables network access. QwenPaw refuses to start a real turn if
that boundary is unavailable. Approval-request rejection and event-level
blocking remain secondary safeguards.

## Troubleshooting

- **Not installed:** verify `codex --version`, or set the absolute binary path.
- **Incompatible:** update the official Codex CLI; QwenPaw detected a missing
  required protocol method or restricted readable-root sandbox capability.
- **Login expired:** start a new browser or device-code login session.
- **No models:** confirm the card says connected, then refresh. Availability is
  determined by the current ChatGPT account and workspace.
- **Usage exhausted:** wait for the displayed reset time or review the account
  plan. This is different from a missing model.
- **Runtime crashed:** select **Detect again**. Active turns fail safely and are
  not replayed.
- **Tool bridge unsupported:** update Codex or disable tools for that agent.
- **Request too large:** reduce history, image count/size, or tool schema size.
  QwenPaw checks the complete compact JSON payload before starting the turn.

## Sign out, disable, and uninstall

Select **Sign out** on the card to ask the official App Server to clear its
login. This does not alter other QwenPaw providers. To disable the integration,
set `QWENPAW_CODEX_SUBSCRIPTION_ENABLED=false` and restart QwenPaw. To disable
dynamic tools for latency diagnosis, set the Codex subscription setting
`codex_dynamic_tool_mode` to `off`; use `all` to restore the full catalog.
In `off` mode QwenPaw sends no `dynamicTools` field and forces tool choice to
`none`. This setting affects only the `openai-codex` provider.

Removing QwenPaw does not delete Codex's own authentication storage. Removing
Codex does not modify QwenPaw's other model providers.

## Security and privacy

QwenPaw stores only non-secret preferences such as the executable path, login
flow preference, tool timeout, and selected model configuration. It does not
read or copy Codex's authentication cache and does not persist access tokens,
refresh tokens, ChatGPT cookies, Cloudflare cookies, or device codes. Logs mask
email addresses and common credential shapes, omit full prompts and tool
arguments, and never include raw App Server stderr.
