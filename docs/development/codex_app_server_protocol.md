# Legacy Codex App Server route

The App Server inference route has been removed from production. QwenPaw no
longer starts Codex processes, creates Codex threads or turns, uses dynamic
App Server tools, or wraps history in XML. This file remains only as a
migration marker for links from older release notes.

The supported architecture is documented in
`docs/openai_codex_subscription.md`: a QwenPaw-native `ChatModelBase` using a
direct compatibility Responses SSE transport, with QwenPaw as the sole agent
runtime.
