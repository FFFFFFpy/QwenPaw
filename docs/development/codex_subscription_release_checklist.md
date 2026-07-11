# ChatGPT/Codex subscription release checklist

## Automated gates

- Direct text/reasoning/tool SSE parsing, usage and cancellation
- PKCE, state expiry/single use, callback validation and refresh rotation
- Encrypted atomic token storage, account isolation and single-flight refresh
- Text, tool result, structured output and image request mapping
- Fixed-host HTTP transport, one 401 refresh, no replay after partial output
- Provider/API/UI catalog and fixed 256K/90% context policy
- Other provider and agent wrapper regression tests
- GPT Image 2 byte validation, size limits, reference-image permission checks,
  duplicate suppression, job status and cancellation
- Model-management search, chat/image grouping, per-model settings and exact
  capability labels

## Manual release gates

Use `gpt-5.6-luna` with low effort: PONG, multi-turn PONG2, one and two
read-only tools, image input, structured output, stop generation and forced
refresh. Then generate a PNG, edit it with the generated file as a reference,
confirm a normal draw request calls `image_generate`, and confirm an explicit
SVG request stays in chat. Record timings, MIME types, byte counts and event
names only; never prompts, tokens, callback queries, Base64 or full account
IDs.

If the live-account matrix is not complete, the release must say so. The
route is a compatibility integration and must not be described as a public
stable OpenAI API.

## Rollback

Set `QWENPAW_OPENAI_CODEX_DIRECT_ENABLED=false`. Keep provider configuration
and Secret Store records. Do not fall back to App Server, API key or a
different model.
