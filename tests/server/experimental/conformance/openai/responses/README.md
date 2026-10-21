# Responses conformance and classification review

The typed declarations are policy decisions. Their exported schema agreeing
with them does not prove that those decisions match the provider. Review three
independent inputs: the pinned provider document, its examples, and native
recordings. A field appearing in a response is evidence of compatibility needs,
not automatic permission to treat its contents as opaque.

## Evidence

- [Pinned OpenAI document](https://github.com/openai/openai-openapi/blob/df63773f69f542ef875b9f00c3837c25ba5f4f2a/openapi.yaml):
  component properties, including inherited `allOf` properties, and the
  `/responses` text-input response example.
- [Current Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create):
  response metadata semantics. The reference and recordings can be newer than
  the pinned component schema; record discrepancies explicitly.
- Native OpenAI recordings under
  `tests/recorded/server/experimental/openai/cassettes/test_responses/`:
  one buffered success, one SSE success, and invalid-model errors requested
  in both modes. The source URL is `https://api.openai.com/v1/responses`;
  no gateway or LiteLLM endpoint participates in these captures.

- [LiteLLM source and recording inventory](../../../../../../nemoguardrails/server/experimental/providers/openai/dialects/litellm/responses.md):
  pinned public source and gateway provenance. `dialects/test_litellm.py` checks
  explicit selection, inherited native metadata, event annotations, unsupported
  containers and error shapes, and relay behavior against this reviewed scope.

`store` is missing from the pinned `Response` component but present in its
text-response example. The native captures additionally contain
`access_programs`, `billing`, `frequency_penalty`, `presence_penalty`, and
`tool_usage`. These names must not be classified as gateway-exclusive just
because a gateway recording first exposed them.

| Surface | Decision | Reason and review probe |
| --- | --- | --- |
| Request `input` | Guarded | The single supported user text subject must reach the checker unchanged. |
| Request instructions, prompt, history, tools, include | Disabled | These can introduce uninspected context, content, or tool execution; reject before dispatch. |
| Request reasoning/text controls | Constrained objects | Permit reviewed controls while refusing summaries and structured-output instructions. |
| Response `output` and message `content` | Guarded containers | Select exactly one assistant output text even when reasoning appears before it. |
| Output text and SSE deltas | Guarded text | A rejecting checker must withhold the rejected text and subsequent snapshots. |
| Done/part/item/lifecycle snapshots | Checked snapshots | These repeat text; never approve a changed snapshot as opaque metadata. |
| Reasoning summary/content, citations, logprobs | Constrained | Reject nonempty additional text channels. |
| Reasoning encrypted state | Opaque | Preserve bytes; no claim of inspecting encrypted content. |
| Response `store`, penalties | Opaque | Echoed storage and sampling controls; they do not select guarded text. |
| Response access programs/billing/tool usage | Opaque | Access/accounting metadata; accepting these containers does not permit tool-call output items. |
| Response instructions/prompt/tools and other echoed settings | Opaque | Request-side gates remain responsible for admission; response echoes are not additional assistant subjects. Review this limitation explicitly. |
| Unknown fields, case aliases, unknown events | Rejected | Recordings do not authorize automatic expansion of the accepted surface. |

Opaque means the entire value is uninspected, including any nested strings. It
does not mean the field is harmless, schema-validated, or approved for arbitrary
new semantics. A metadata-to-content change requires a new policy decision.

## Reproduce the source inventory

The compact fixture is extracted from provider source, independently of the
runtime models. The extractor verifies the digest in `contracts/openai/source.yaml`.
It performs no downloads or provider calls. From the repository root:

```bash
curl --fail --location \
  https://raw.githubusercontent.com/openai/openai-openapi/df63773f69f542ef875b9f00c3837c25ba5f4f2a/openapi.yaml \
  --output /tmp/openai-responses-source.yaml
uv run --locked python tests/server/experimental/conformance/openai/responses/fixtures/extract_source.py \
  /tmp/openai-responses-source.yaml \
  nemoguardrails/server/experimental/contracts/openai/source.yaml \
  tests/server/experimental/conformance/openai/responses/fixtures/source_inventory.json
make test TEST=tests/server/experimental/conformance/openai/responses/test_schema.py
```

The [fixture provenance](fixtures/README.md) documents the digest and extraction scope.
The source audit traverses the actual bound request, response, and event models;
export agreement is secondary. It compares exact field sets, accounting explicitly
for six native response fields, native delta `obfuscation`, and the dialect event
`model` annotation.

The fixture covers the selected Responses object boundaries, not every OpenAI
schema or unsupported tool/event family. Changing the source pin requires
regenerating and reviewing the fixture; do not regenerate expected policy
decisions from runtime output.

## Record and replay

The credential must already be available to the test process. Do not place it
in commands, test data, logs, or review reports.

```bash
make record-cassettes \
  RECORDED_TESTS=tests/recorded/server/experimental/openai/test_responses.py \
  RECORDED_REQUIRED_KEYS=OPENAI_API_KEY
make replay-cassettes \
  RECORDED_TESTS=tests/recorded/server/experimental/openai/test_responses.py
```

Capture completes before projection validation so new fields are retained for
review. The mandatory offline replay then validates the contract and routing.
Each pass consumes exactly one interaction; explicit rewinds separate direct,
inspected, and uninspected comparisons. Unlimited playback is not enabled.
The shared sanitizer filters credentials and selected metadata; it preserves
nested IDs and content. Equality refers to sanitized replay bytes, not original
network chunking, whitespace, or live delivery timing.

## Independent reviewer probes

Run the executable probes without credentials or network access:

```bash
make test WORKERS=0 \
  TEST="tests/server/experimental/conformance/openai/responses tests/recorded/server/experimental/openai/test_responses.py tests/recorded/server/experimental/openai/test_litellm_responses.py" \
  ARGS="--block-network --record-mode=none"
```

The recorded suite reuses the successful cassettes for first/later-window
blocking and overlapping context. Those tests force replay mode even during
refresh. Request mutation probes in this conformance suite verify rejection
before dispatch with either endpoint, including when inspection is disabled.
The mutation suite changes copies of recorded bodies in memory and injects them through HTTPX MockTransport; it never
writes or records the altered payloads. Unmodified positive controls exercise
the same injection path. These are synthetic rejection probes, not evidence
that OpenAI actually emitted those unsupported bodies.

A reviewer or review agent should inspect evidence before reading the current
classification table. Use these questions as separate review passes:

1. **Completeness:** enumerate source properties and example/recorded keys at
   each object boundary. Report omissions with exact paths and provenance.
   Distinguish native metadata from gateway annotations; response metadata is shared,
   while root SSE `model` requires the selected dialect. Check lifecycle
   responses as well as buffered responses.
2. **Text admission:** attempt additional input, history, tool arguments,
   reasoning summaries, refusals, citations, and duplicate output text. For
   each accepted case, identify which checker sees the text. An accepted text
   channel without inspection needs an explicit scope decision or a fix.
3. **Streaming:** block the first and a later delta; verify only the approved
   prefix escapes. Change a final snapshot while preserving deltas and verify
   rejection. Metadata events must not introduce a second content channel.
4. **Evidence quality:** verify native origin, request model and controls,
   sanitization, exact interaction counts, and offline-only replay. Synthetic
   verdicts and payload mutations must be labeled separately from recordings.

Return a path, source/cassette reference, minimal reproduction, expected versus
actual behavior, and proposed classification for each finding. A passing test
suite or export comparison alone is not a semantic review. These instructions
prepare independent review; they do not claim that such a review has run.
