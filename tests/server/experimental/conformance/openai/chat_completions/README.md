# Chat conformance and classification review

This suite checks independent provider evidence, our typed field classifications,
and behavior through the actual Chat router and HTTP dispatcher. It covers native
OpenAI and the explicitly selected LiteLLM dialect. It does not certify every
backend, every field value, real Rails configuration, or YAML application wiring.

## Evidence

- [Pinned OpenAI source extract](fixtures/README.md): the six Chat operations and
  reachable schemas, with provenance, digest, and a reproducible extractor.
- [Reviewed LiteLLM source inventory](../../../../../../nemoguardrails/server/experimental/providers/openai/dialects/litellm/README.md):
  source-backed extension decisions and the distinction between buffered and
  streaming bags. An open upstream dictionary does not authorize opaque content.
- [Recorded Chat suite](../../../../../recorded/server/experimental/openai/test_chat_completions.py):
  four native success/error cassettes and six archived gateway cassettes, with
  [capture provenance](../../../../../recorded/server/experimental/README.md).

Native captures use the base endpoint. The archived buffered gateway response
has a message `provider_specific_fields` bag with `refusal: null` and an empty
choice bag; it requires the dialect when inspected. The archived gateway stream
has no extension bags and passes either endpoint. Do not infer a dialect from
provider hostnames or assume every gateway stream has additional fields.

### Pinned source discrepancies

The native buffered choice omits `logprobs`, which the pin marks required.
Both native modes return `system_fingerprint: null`, while the pinned schema
requires a string when present. `test_native_recordings_expose_exact_pinned_schema_discrepancies`
asserts precisely these validation differences. Recordings and source bytes
remain unchanged. Any new difference requires review; tests do not silently
normalize payloads or weaken the independent source validator.

The official [buffered response reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
and [streaming reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events),
checked on 2026-10-22, retain the same restrictions: buffered `logprobs` is
required and nullable, while `system_fingerprint` is an optional string.
The discrepancy is not established to be an outdated pin; its upstream cause
remains unconfirmed.

A passing discrepancy test means the recording has exactly the documented
schema errors, not that it conforms to the schema. Separate replay tests verify
runtime compatibility: omitted or null `logprobs` is accepted, populated
logprobs is rejected under inspection, and `system_fingerprint` is preserved
as opaque metadata. Synthetic source-valid cases still validate independently.

## Coverage and boundaries

- `test_schema.py` audits every native bound model against source properties,
  verifies guarded/constrained/opaque decisions for both endpoints, and checks
  the complete LiteLLM graph against the reviewed extension inventory.
- `test_http.py` exercises independently source-valid synthetic cases.
- `dialects/test_litellm.py` tests closed extension bags and empty-only content
  fields, including usage-only streaming events.
- `test_recorded_probes.py` keeps unmodified recordings as positive controls,
  then mutates copies in memory to try additional reasoning/refusal text,
  case aliases, logprobs, extra choices, and request controls. It checks both
  inspected rejection and exact uninspected relay. No mutations are recorded.
- Recorded tests enforce exact replay consumption, provider-error preservation,
  buffered blocking, first/later streaming blocking, and overlapping context.

`usage`, identity, and reviewed accounting metadata are opaque, including nested
values. The probe contrasting text inside `usage` with text beside `usage` makes
that limitation explicit: opaque values are neither inspected nor certified
safe. Reasoning-token counts are metadata; plaintext reasoning is constrained.
Buffered choice `index` is opaque; streaming `index` is constrained to zero.
Chat errors use a nested error object and `[DONE]`; Responses uses different
framing. Unknown content fails closed under the default inspected policy.

## Run and review

```bash
make test WORKERS=0 \
  TEST="tests/server/experimental/conformance/openai/chat_completions tests/recorded/server/experimental/openai/test_chat_completions.py tests/recorded/server/experimental/openai/test_litellm_inventory.py" \
  ARGS="--block-network --record-mode=none"
```

Independent reviewers or review agents should perform these passes:

1. Enumerate source and observed keys before reading our classifications.
   Check every nested boundary, source discrepancy, and native/dialect difference.
2. Trace each possible text channel to its checker or explicit rejection rule.
   Inspect reasoning, refusals, tools, citations, logprobs, participant names,
   extension bags, and fields that affect routing or guardrail selection.
3. Probe usage-only chunks, additional choices, case aliases, and blocked
   streams. Verify exact approved prefixes and terminal/error framing. Check
   that disabling response inspection does not bypass request admission.
4. Check capture origin, selected model, sanitizer behavior, one-interaction
   accounting, and separation of real evidence from synthetic mutations.

Report the field path, evidence reference, minimal reproduction, expected and
actual behavior, and proposed decision for each finding. Passing schema/export
checks alone do not establish semantic correctness. These instructions prepare
independent review; they do not claim a separate reviewer has run.
