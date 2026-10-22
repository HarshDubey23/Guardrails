# Offline provider conformance

These tests compare an independently pinned provider specification, authoritative
typed Python declarations, and observable proxy behavior. They currently cover
OpenAI Chat Completions and Responses, each with an explicitly selected LiteLLM
dialect. They do not establish compatibility with every OpenAI-compatible deployment.

## Coverage

- [Chat Completions](openai/chat_completions/README.md): source and classification
  audits, HTTP behavior, observed dialect extensions, and mutations of recordings.
- [Responses](openai/responses/README.md): source/example inventories, native and
  dialect boundaries, request admission, text channels, and snapshot consistency.

Tests use deterministic checker doubles and mocked network I/O. Guard models are
partial policy projections, not complete provider validators. Disabling output
inspection preserves guarded-request admission. Real Rails configuration, socket
delivery, and live-provider behavior require separate coverage.

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

## Reviewer probes

Reviewers and review agents should inspect evidence before our classifications:

1. Enumerate source, example, and recorded keys at each nested boundary. Check
   omissions, discrepancies, and native/dialect distinctions.
2. Trace possible text channels to inspection or explicit rejection: instructions,
   history, tools, reasoning, refusals, citations, logprobs, participant names,
   and extension bags. Review routing and guardrail-selection controls.
3. Probe unknown fields, case aliases, extra outputs, first/later blocking, and
   final snapshots. Verify the exact approved prefix and protocol framing, plus
   request admission with output inspection disabled.
4. Verify capture origin, model/controls, sanitization, and interaction counts.
   Distinguish genuine provider evidence from synthetic verdicts and mutations.

Report field path, evidence reference, minimal reproduction, expected/actual
behavior, and proposed classification. Schema/export agreement alone does not
establish semantic correctness.

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
