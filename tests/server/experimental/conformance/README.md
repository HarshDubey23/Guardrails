# Offline provider conformance

These tests compare an independently pinned provider specification, authoritative
typed Python declarations, and observable proxy behavior. They currently cover
OpenAI Chat Completions and Responses, each with an explicitly selected LiteLLM
dialect. They do not establish compatibility with every OpenAI-compatible deployment.

## Coverage

- `openai/chat_completions/test_schema.py` audits every bound policy model
  against its inspected source object, including reviewed opaque fields and
  explicit local extensions. It also checks explicit content classifications for
  both endpoints and the complete LiteLLM model graph. Export consistency is secondary.
- `openai/chat_completions/test_http.py` exercises the actual router and HTTP
  dispatcher with synthetic source-valid examples. It checks metadata and byte
  preservation, exact inspected text, unsupported content, error envelopes, and
  buffered/streaming behavior with inspection enabled or disabled.
- `openai/chat_completions/dialects/test_litellm.py` checks dialect selection, extension
  preservation, rejection of unreviewed content, and unchanged text targets
  through the same HTTP harness. Cases follow the
  [reviewed LiteLLM source inventory](../../../../nemoguardrails/server/experimental/providers/openai/dialects/litellm/README.md),
  not the OpenAI schema or additional recordings.
- `openai/chat_completions/test_recorded_probes.py` adds native/gateway positive
  controls and synthetic mutations of recorded bodies. It makes pinned-source
  discrepancies explicit and checks additional text, usage-only events, request
  admission, and dialect selection. See the [Chat review guide](openai/chat_completions/README.md).

- `openai/responses/test_schema.py` audits bound request, response, and event
  models against the pinned source inventory and official example, with explicit
  native additions. Export consistency remains a secondary check.
- `openai/responses/test_http.py` injects labeled synthetic mutations of native
  recordings through the actual HTTP path. It checks request admission, additional
  text channels, and snapshot consistency with inspection enabled and disabled.
- `openai/responses/dialects/test_litellm.py` checks the selected dialect against
  gateway evidence, shared native metadata, root event annotations, and unsupported
  containers/errors. The [Responses guide](openai/responses/README.md) includes
  provenance, reproduction commands, and independent reviewer probes.

`openai/chat_completions/http_support.py` shares the Chat router/transport harness and error
assertions; `openai/chat_completions/support.py` holds Chat payload and source helpers. Checker
doubles remain shared across server tests in `../checkers.py`. Responses probes
reuse `../responses_proxy.py`, also used by the recorded suites; their evidence
loaders and mutation helpers live in `openai/responses/support.py`.

Disabling inspection does not remove guarded-request validation. Unknown output
content fails closed when inspected and is relayed unchanged when uninspected.
Existing recorded and kernel tests cover checker blocking, stream sequencing,
and cleanup; this suite adds independent source evidence rather than duplicating
those tests.

These are offline tests with deterministic checker doubles and mocked network
I/O. They do not validate real Rails configurations, socket delivery, application
lifecycle, or current live-provider behavior. Guard models are partial policy
projections, not complete provider validators; field coverage is not exhaustive
value or combination coverage.

## Important limitations

The router guards `POST /v1/chat/completions`. The documented collection operation,
`GET /v1/chat/completions`, returns 405 at this guarded path. The four documented
item operations on `/v1/chat/completions/{id}` and its `/messages` child are relayed
without guardrails. Their tests check transport preservation, not full provider-schema
validation.

The [pinned source](openai/chat_completions/fixtures/README.md) declares OpenAPI 3.1 but uses
legacy `nullable` annotations, including beside enums and references.
`openai/chat_completions/support.py` explicitly interprets them for validation without
changing fixture bytes or runtime policy. This adapter supports the constructs
used by this pin, not arbitrary OpenAPI documents. Review its interpretation
when updating the source.

## Extending coverage

For another operation or dialect, identify independent source evidence, audit
the actual bound models, and supply explicit valid and unsupported examples with
independent expected outcomes. Reuse checker helpers and existing kernel tests;
keep event sequencing, terminal markers, and error envelopes protocol-specific.

## Running tests

```bash
make test WORKERS=0 \
  TEST='tests/server/experimental/conformance' \
  ARGS='--block-network -q'
```

To include related kernel, export, dialect, transport, and recorded tests:

```bash
make test WORKERS=0 \
  TEST='tests/server/experimental tests/recorded/server/experimental' \
  ARGS='--block-network -q'
```
