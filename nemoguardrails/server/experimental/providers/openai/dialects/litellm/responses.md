# LiteLLM Responses dialect

The experimental [typed declaration](responses.py) binds the existing Responses
single-text policy to a reviewed LiteLLM gateway surface. It is selected explicitly;
the strict OpenAI endpoint and the LiteLLM Chat dialect are unchanged. No LiteLLM
package, new transport, new kernel, or alternate lifecycle hooks are required.

## Policy difference

| Location | Decision |
| --- | --- |
| Request | Reuse the strict Responses binding unchanged: one string input, foreground generation, no tools, conversation history, or additional plaintext instructions. |
| Buffered response and lifecycle `response` snapshots | Reuse the native response policy, including opaque `access_programs`, `billing`, `frequency_penalty`, `presence_penalty`, `store`, and `tool_usage` metadata. |
| Root of each reviewed SSE event | Recognize optional `model` as opaque metadata, never as an event selector or text source. |
| Output items, content parts, delta text, annotations, logprobs, reasoning summaries | Inherit the existing constrained/guarded policy unchanged. No Chat-specific `provider_specific_fields` bags are added. |
| Event selection and lifecycle | Retain all 15 existing event rules, derived paths/coverage, native terminal requirements, identity checks, snapshot consistency, and error framing. |

Opaque metadata is passed through **without inspecting its values or validating
its nested structure**. For example, `tool_usage` is accounting metadata, not
permission to emit tool calls. The actual output items still reject tool content.
Allowing `store` in the response does not introduce a new request control: it was
already reviewed in the base request declaration.

Native OpenAI recordings also contain all six response metadata names. They
belong to the base Responses policy; the dialect adds only the root SSE `model`
annotation. Buffered response and nested lifecycle response declarations are
shared with the base endpoint.
Unknown root members, new events and unsupported content still fail closed.
The base profile's trusted nested unknown-field override remains unchanged.

## Explicit composition

Use the existing application assembly with trusted endpoint declarations:

```python
from dataclasses import replace

from nemoguardrails.server.experimental.providers.openai.integration import create_openai_integration
from nemoguardrails.server.experimental.providers.openai.dialects.litellm.chat_completions import (
    LITELLM_CHAT_COMPLETIONS_ENDPOINT,
)
from nemoguardrails.server.experimental.providers.openai.dialects.litellm.responses import (
    LITELLM_RESPONSES_ENDPOINT,
)

integration = replace(
    create_openai_integration(base_url="https://your-gateway.example/v1"),
    endpoints=(LITELLM_CHAT_COMPLETIONS_ENDPOINT, LITELLM_RESPONSES_ENDPOINT),
)
```

Supply this integration to the existing proxy application alongside its Rails
configuration. Credentials use the application's existing HTTP authorization
path; neither YAML exports, model strings nor gateway hostnames select a dialect.
This is the transparent HTTP path, not a LangChain or LiteLLM client integration.

## Source and recording evidence

On 2026-10-15 the authenticated deployment OpenAPI advertised LiteLLM `1.105.1`.
Both `/responses` and `/v1/responses` declared `ResponsesAPIResponse` for buffered
success and a string SSE body for streaming. The latter is not an event schema.
The private schema is not vendored, and its advertised version does not establish
the absence of deployment patches.

The matching public tag resolves to
[`d665dc1a6315847fcad6cc2c1264ceb47c06abce`](https://github.com/BerriAI/litellm/tree/d665dc1a6315847fcad6cc2c1264ceb47c06abce):

- [`litellm/types/llms/openai.py`](https://github.com/BerriAI/litellm/blob/d665dc1a6315847fcad6cc2c1264ceb47c06abce/litellm/types/llms/openai.py)
  declares `ResponsesAPIResponse`, lifecycle events and a separate nested error
  shape. SHA-256: `99ce22ba2bb83dc37d9afb423ef2a6cf919a9cafca834eaad833c4a795088fdd`.
- [`litellm/llms/openai/responses/transformation.py`](https://github.com/BerriAI/litellm/blob/d665dc1a6315847fcad6cc2c1264ceb47c06abce/litellm/llms/openai/responses/transformation.py)
  constructs a Responses object from native OpenAI JSON, with a construction
  fallback on validation failure. SHA-256: `7c6f3bea7678b76b6e99bc5b4c499eef9aead8947a6a81e534dc5e42c84bfc70`.

Success cassettes live in
`tests/recorded/server/experimental/openai/cassettes/test_litellm_responses/`.
They capture `openai/openai/gpt-5-nano` through NVIDIA's inference gateway, with a
harmless greeting, `store: false`, and a 256-token output limit. Buffered evidence
includes all six shared metadata fields. Streaming evidence includes the model
annotation and initial/final snapshots, reasoning-item metadata, assistant-item
snapshots, content-part snapshots and text deltas/done. No new event type or Chat
extension bag was observed. They are evidence for this route, not raw vLLM or
every LiteLLM backend.

The [capture provenance](../../../../../../../tests/recorded/server/experimental/README.md#capture-provenance)
also documents invalid-model HTTP errors and the longer stream used for
overlapping-window blocking.

The [LiteLLM Responses documentation](https://docs.litellm.ai/docs/response_api)
also describes Chat-to-Responses bridging. These cassettes do not establish
compatibility with bridged routes. Nested LiteLLM SSE errors, additional content
channels, and other backend-specific fields remain unsupported and fail closed.
Flat diagnostic errors and other inherited events have synthetic unit coverage;
they are not claimed as recorded observations.

## Validation and export

```bash
NVIDIA_INFERENCE_KEY=... make record-cassettes \
  RECORDED_TESTS=tests/recorded/server/experimental/openai/test_litellm_responses.py \
  RECORDED_REQUIRED_KEYS=NVIDIA_INFERENCE_KEY

make replay-cassettes \
  RECORDED_TESTS=tests/recorded/server/experimental/openai/test_litellm_responses.py

uv run --locked python -m nemoguardrails.server.experimental.provider.contract_export \
  nemoguardrails.server.experimental.providers.openai.dialects.litellm.responses:LITELLM_RESPONSES_ENDPOINT \
  --output nemoguardrails/server/experimental/contracts/openai/_generated/litellm-responses.guard.yaml
```

Capture alone does not establish compatibility; mandatory offline replay checks
the selected endpoint and its allow/block behavior. See the
[recording guide](../../../../../../../tests/recorded/server/experimental/README.md)
for replay guarantees and evidence limits. Synthetic unit tests cover unsupported
content, errors, cancellation, and malformed streams.
The exported YAML describes the effective declarations and is not a runtime input.
