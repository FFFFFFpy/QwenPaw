# Release note: OpenAI Codex subscription provider

QwenPaw now includes **OpenAI Codex** as a built-in cloud-subscription
provider. Users can connect an eligible ChatGPT account with the official
Codex App Server, discover account models, view usage windows, select reasoning
effort, send image inputs, stream responses, cancel turns, and use QwenPaw tools
through the dynamic-tool bridge.

Authentication remains entirely within the official App Server. QwenPaw does
not store ChatGPT OAuth tokens or read Codex authentication files. The feature
can be rolled back independently with
`QWENPAW_CODEX_SUBSCRIPTION_ENABLED=false`.

Known limitation: dynamic tools depend on the installed App Server's
experimental protocol capability. Browser and device-code login availability
can also be restricted by ChatGPT workspace policy.

