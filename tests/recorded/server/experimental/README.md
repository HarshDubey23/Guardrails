# Recorded proxy integration tests

These tests answer: do observed provider exchanges work through the selected
proxy endpoint, and are checker decisions enforced on those exchanges?
They exercise the router, projections, stream classifier/hooks, and HTTP
dispatcher. A configurable checker double supplies synthetic decisions; this
is not evidence that a real Rails configuration produces those verdicts.

## Current Chat coverage

The [native Chat suite](openai/test_chat_completions.py) selects the native OpenAI
endpoint for direct OpenAI recordings. The
[LiteLLM Chat suite](openai/test_litellm_chat_completions.py) selects the LiteLLM
dialect for the archived NVIDIA inference exchanges. Its six cassettes live in
`openai/cassettes/test_litellm_chat_completions/`; their contents are unchanged.
The [conformance inventory](../../../server/experimental/conformance/openai/chat_completions/dialects/test_litellm_inventory.py)
reads these cassettes to pin which extension shapes actually occurred and check
runtime/export acceptance. It runs with the conformance suite, not cassette replay.

Recorded modules use `test_{operation}.py` for native OpenAI and
`test_{dialect}_{operation}.py` for explicitly selected dialects. Each module's
cassettes live under `openai/cassettes/{module_stem}/`. Responses follows the
same convention with `test_responses.py` and `test_litellm_responses.py`.

| Scenario | Evidence and assertion |
| --- | --- |
| Buffered and streaming allow | Recorded payloads pass the selected boundary; extracted input/output text reaches the checker. |
| Inspected versus uninspected relay | Status, replay body bytes, and relevant headers match, with explicit encoding/header exceptions. |
| Provider errors in either mode | Recorded error response is preserved; no output check occurs. |
| Buffered output block | A synthetic rejection withholds the recorded assistant response. |
| First streaming window blocked | No provider payload events escape; the proxy emits a blocking error and Chat terminal marker. |
| Later streaming window blocked | Only the approved provider event prefix escapes, followed by the blocking error and terminal marker. No rejected or subsequent provider payload is released. |
| Overlapping streaming context | Checker inputs include the configured preceding context; that context is not duplicated in released events. |

The input-block/no-dispatch invariant is already covered by the offline router
integration in `tests/server/experimental/test_provider_transport.py`. It does
not need a provider recording because the provider must never be called.

Each replay pass consumes exactly one upstream interaction. Allowed-path
comparison explicitly checks consumption, rewinds the cassette once, and then
checks the uninspected pass separately. Unrestricted playback repeats are not
enabled, so extra dispatches or HTTP retries cannot reuse the recorded response.

## Native Chat evidence

Four `test_native_chat_*` cassettes in `openai/cassettes/test_chat_completions/`
were captured directly from `https://api.openai.com/v1/chat/completions` on
2026-10-21: buffered/streaming success and invalid-model errors in both modes.
The success profile uses `gpt-5-nano-2025-08-07`, one benign greeting, minimal
reasoning, a 256-token completion budget, and `store: false`. Streaming requests
include usage. This snapshot is a historical compatibility baseline matching the
existing gateway model family, not a model recommendation.

The native response contains ordinary message `refusal`/`annotations` fields;
stream events contain root `obfuscation` and a final usage-only chunk. No
`provider_specific_fields` container occurs in these native captures. These are
observations for this model and request, not a universal native/gateway schema.

The shared Chat helper selects endpoint and upstream explicitly. Native capture
finishes before proxy validation; mandatory offline replay checks exact sanitized
bytes, checker inputs, and one interaction per pass. Native successful cassettes
also exercise buffered rejection, first/later streaming rejection, and overlapping
context. Blocking tests force replay and never create synthetic recordings.

To refresh only the four native captures with the credential already available:

```bash
make record-cassettes \
  RECORDED_TESTS="tests/recorded/server/experimental/openai/test_chat_completions.py::test_native_chat_preserves_policy_and_wire tests/recorded/server/experimental/openai/test_chat_completions.py::test_native_chat_provider_error_is_preserved" \
  RECORDED_REQUIRED_KEYS=OPENAI_API_KEY
make replay-cassettes \
  RECORDED_TESTS=tests/recorded/server/experimental/openai/test_chat_completions.py
```

The capture sanitizer preserves a null `system_fingerprint`, substitutes a
placeholder for non-null fingerprints, and does not remove `logprobs`.
The [Chat conformance guide](../../../server/experimental/conformance/openai/chat_completions/README.md#pinned-source-discrepancies)
documents the observed differences from the pinned schema and current reference.

Future captures preserve `openai-version` when returned. This API version does
not identify an exact OpenAPI revision or freeze response fields; see the
[API version and compatibility documentation](https://developers.openai.com/api/reference/overview).
Record the capture date, endpoint, requested and returned model, and comparison
schema revision in the capture provenance. Keep request IDs privately when
needed for provider support; request IDs, organization headers, and response
dates remain filtered from committed cassettes. The existing 2026-10-21 captures
predate version-header retention and remain unchanged; their removed headers
cannot be recovered from these recordings.

Replay equivalence refers to sanitized bytes, not network chunking or live
delivery timing. No additional SDK dependency is required. Gateway recordings
retain their separate provenance and credential.

## Native Responses coverage

The [Responses suite](openai/test_responses.py) calls native OpenAI during
authorized capture and selects the base Responses endpoint during offline replay.
Its four cassettes cover buffered/streaming success and genuine invalid-model
errors requested in both modes. Successful recordings are reused with synthetic
checker verdicts for buffered blocking, first/later streaming-window blocking,
and overlapping context.
Responses errors use native SSE framing without a Chat `[DONE]` marker.

Blocking tests reference the existing successful cassettes and force
`record_mode="none"`. They cannot refresh or manufacture evidence. Refresh the
original success tests when authorized; do not create separate provider
recordings for synthetic safety verdicts.

## Pattern for another operation or provider

1. Keep tests and sanitized recordings under the provider directory. Record
   provenance, the selected endpoint/dialect, supported modes, and observed
   field shapes. Do not infer all-backend compatibility from one deployment.
2. Bind the actual operation to the real router and dispatcher. Use a fresh
   [StaticChecker](../../../server/experimental/checkers.py) for each request
   sequence. It captures full check objects and exposes text observations;
   assert exact calls because its default output decision is allow.
3. Start with allowed buffered/streaming exchanges and a genuine provider
   error where those modes apply. Assert independently expected extracted text,
   request contents, response contents, and upstream interaction counts.
4. Reuse successful recordings for representative rejection cases. Script the
   checker decisions, not the provider payload. Derive the expected approved
   prefix from recorded events, independently of the runtime classifier. For
   overlapping windows, assert individual checker inputs rather than joining
   them into an output string.
5. Keep protocol-specific assertions local: Responses and Anthropic have their
   own event ordering, text locations, and terminal/error conventions. Do not
   reuse Chat's `[DONE]` requirement or `choices` paths.
6. Share small helpers when a second operation demonstrates identical needs.
   Reuse checker doubles and scenario intent; do not copy every kernel test or
   add an adapter hierarchy merely to make unlike protocols look alike.

Synthetic Chat payload builders live separately in
[chat_fixtures.py](../../../server/experimental/chat_fixtures.py); these are not
provider recordings. Tests import support modules, not other test modules.

## Evidence limits and complementary tests

Cassette sanitization reconstructs JSON and supported SSE representations.
Replay equality proves preservation of that representation, not original wire
whitespace, network chunks, or live delivery timing. Both compared paths share
code, so their agreement is not an independent oracle for every transport bug.

- Raw-byte transport tests must cover original bytes, Unicode, framing, and
  upstream request observations without comparison normalization.
- Real-config application tests must cover the production Rails adapter and
  actual allow/block verdicts, first with deterministic rails and then with
  representative recorded model-backed checks. Keep generation and safety
  service interactions distinguishable.
- Localhost application tests must establish incremental delivery, socket
  disconnect handling, and ownership. These in-process replay tests do not
  establish startup/shutdown or interrupted-cleanup correctness.
- Synthetic dialect/kernel tests cover malformed or unsupported shapes and
  state transitions that are absent from the recordings.

Real-config and localhost coverage belongs with the later application/example
work, using the existing assembly. Do not introduce a second application
implementation here or treat this suite as complete proxy conformance.

## Offline validation

From the repository root:

```bash
make replay-cassettes RECORDED_TESTS=tests/recorded/server/experimental/openai
```

This blocks live network access. Missing or exhausted recordings fail the test.
See the [recording guide](../../README.md) for the separately authorized refresh
workflow, credential sanitization, and source cassette inventory.
