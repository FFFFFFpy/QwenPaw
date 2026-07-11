# Release note: Experimental OpenAI Codex subscription provider

QwenPaw includes an **experimental OpenAI Codex** cloud-subscription
provider. Users can connect an eligible ChatGPT account with the official
Codex App Server, discover account models, view usage windows, select reasoning
effort, send image inputs, stream responses, cancel turns, and use QwenPaw tools
through the experimental dynamic-tool bridge.

The adapter validates the installed App Server schema, uses the official
read-only network-disabled turn policy, disables Codex built-in tools through
thread-local config overrides, serializes cancellation with a cleanup barrier,
budgets each JSON-RPC request independently, validates reasoning effort, and
keeps provider listing free of Runtime and authentication I/O.

Authentication remains entirely within the official App Server. QwenPaw does
not store ChatGPT OAuth tokens or read Codex authentication files. The provider
is disabled by default. Enabling it requires
`QWENPAW_CODEX_SUBSCRIPTION_ENABLED=true` and a recorded successful tool
isolation probe for the installed capability fingerprint.

Dynamic tools and the verified built-in-tool shutdown configuration depend on
the installed App Server version. Unverified versions must not be activated.
Browser and device-code login availability can also be restricted by ChatGPT
workspace policy.
