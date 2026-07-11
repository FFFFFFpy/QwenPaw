# Release note: ChatGPT/Codex subscription direct transport

ChatGPT/Codex subscription is integrated as a QwenPaw-native ChatModel
through a compatibility Responses transport. QwenPaw remains the sole
agent runtime.

The provider supports PKCE login (including a manually pasted cross-machine
callback), encrypted refresh-token storage, direct text/reasoning/tool SSE,
structured output and images, account-scoped model errors, and the fixed 256K
QwenPaw context policy. The old App Server inference, thread/turn lifecycle,
dynamic-tool bridge and XML history mapper are removed.

This uses a non-public ChatGPT/Codex backend compatibility route, not the
OpenAI Platform public stable API. Upstream changes can require a QwenPaw
compatibility update. Emergency pause:
`QWENPAW_OPENAI_CODEX_DIRECT_ENABLED=false`.
