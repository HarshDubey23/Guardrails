# Recorded proxy integration tests

These tests replay provider exchanges through the router, projections, stream
classifier, and HTTP dispatcher. A checker double supplies allow/block decisions.
They validate captured exchanges and enforcement, without exercising real Rails
configuration, socket delivery, or live-provider behavior.

## Suites and coverage

Native modules use `test_{operation}.py`; dialect modules use
`test_{dialect}_{operation}.py`. Cassettes live in
`openai/cassettes/{module_stem}/`.

| Suite | Selected endpoint | Capture credential |
| --- | --- | --- |
| [Chat Completions](openai/test_chat_completions.py) | Native OpenAI | `OPENAI_API_KEY` |
| [Responses](openai/test_responses.py) | Native OpenAI | `OPENAI_API_KEY` |
| [LiteLLM Chat Completions](openai/test_litellm_chat_completions.py) | LiteLLM dialect | `NVIDIA_INFERENCE_KEY` |
| [LiteLLM Responses](openai/test_litellm_responses.py) | LiteLLM dialect | `NVIDIA_INFERENCE_KEY` |

Each suite covers buffered/streaming success, provider HTTP errors requested in
both modes, buffered blocking, first/later streaming-window blocking, and
overlapping context. Assertions check extracted text, status, relevant headers,
replay bytes, and the exact approved event prefix. Chat uses `[DONE]`; Responses
uses its own terminal and error framing. HTTP errors are not evidence of failures
after streaming begins.

Blocking tests reuse successful recordings with synthetic checker decisions and
force replay mode. Each comparison pass consumes one upstream interaction;
explicit rewinds separate passes. Input rejection before dispatch and malformed
streams are covered by unit/conformance tests.

## Capture provenance

Native Chat was captured on 2026-10-21 from
`https://api.openai.com/v1/chat/completions`. Its success profile uses
`gpt-5-nano-2025-08-07`, a benign greeting, minimal reasoning, a 256-token budget,
`store: false`, and streaming usage. Native Responses uses
`https://api.openai.com/v1/responses` and the same model and generation controls.
These are historical compatibility fixtures, not model recommendations.

The [Chat conformance guide](../../../server/experimental/conformance/openai/chat_completions/README.md#pinned-source-discrepancies)
documents omitted `logprobs` and null `system_fingerprint` discrepancies.
Sanitization preserves those shapes. Native Chat events also include `obfuscation`
and a usage-only chunk, without gateway extension bags.

LiteLLM Chat uses NVIDIA's inference gateway. Buffered successes contain message
`provider_specific_fields: {refusal: null}` and empty choice extension bags;
streaming successes contain no extension bags. Alternate-prompt captures are
retained as additional evidence. See the
[reviewed dialect source](../../../../nemoguardrails/server/experimental/providers/openai/dialects/litellm/README.md).

LiteLLM Responses uses `https://inference-api.nvidia.com/v1/responses` with model
`openai/openai/gpt-5-nano`, `store: false`, and a 256-token output limit. Its
[source evidence](../../../../nemoguardrails/server/experimental/providers/openai/dialects/litellm/responses.md#source-and-recording-evidence)
pins LiteLLM commit `d665dc1a6315847fcad6cc2c1264ceb47c06abce`; the native OpenAI
comparison pin is `df63773f69f542ef875b9f00c3837c25ba5f4f2a`.
Captures dated 2026-10-22 cover:

- Invalid-model requests in both modes: `openai/openai/recorded-invalid-model`
  returns HTTP 403 `key_model_access_denied`.
- `streaming-long-output`: a longer greeting with minimal reasoning, requesting
  and returning `openai/openai/gpt-5-nano`, to exercise overlapping windows.

Those captures contain no `openai-version` header and do not identify the serving
LiteLLM version. The shared sanitizer retains `openai-version` when returned;
this header does not identify an exact source revision. Record capture date,
endpoint, requested/returned model, and comparison source pin when refreshing.
Request IDs, organization headers, and response dates are filtered; retain
request IDs privately if needed for provider support. Headers removed from older
captures cannot be recovered from their cassettes.

## Record and replay

With the required credential already in the environment:

```bash
make record-cassettes \
  RECORDED_TESTS=tests/recorded/server/experimental/openai/test_litellm_responses.py \
  RECORDED_REQUIRED_KEYS=NVIDIA_INFERENCE_KEY
make replay-cassettes RECORDED_TESTS=tests/recorded/server/experimental/openai
```

Select a module or test node to limit capture. Capture finishes before policy
validation; mandatory offline replay checks acceptance. Never remove unsupported
fields to make replay pass. See the [shared recording guide](../../README.md)
for refresh modes and sanitization. Replay equality applies to sanitized bytes,
not original HTTP chunks, whitespace, or delivery timing. Responses uses the same
sanitizer as Chat, retaining text, encrypted content, nested item IDs, and event
ordering subject to the shared credential filters.

## Extending coverage

Bind the real endpoint/router and reuse the shared checker. Derive expected text
and approved prefixes independently of the runtime classifier. Keep event
ordering and terminal/error assertions specific to the protocol; for overlapping
windows, check each checker input separately. Synthetic mutations belong in
unit/conformance tests and must remain distinct from provider recordings.

Replay can check captured payloads against the selected contract. The
[conformance suite](../../../server/experimental/conformance/README.md) separately
audits source coverage, field classifications, dialect boundaries, and synthetic
variations. Export agreement alone does not establish a correct classification.
