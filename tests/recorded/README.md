# Recorded Tests

Recorded tests replay provider traffic through pytest-recording cassettes and must run without live network access by default.

## Adding a test

Markers are applied once per module via `pytestmark`; do not stack `@pytest.mark.recorded` / `vcr` / `asyncio` on each test. Use a module-level list, and fold in `vcr`/`asyncio` only when every test in the module needs them:

```python
import pytest

from nemoguardrails import LLMRails
from tests.recorded.rails.public_api.configs import OPENAI_BASELINE_CONFIG
from tests.recorded.rails_config import load_config
from tests.recorded.snapshots import snapshot

pytestmark = [pytest.mark.recorded, pytest.mark.vcr, pytest.mark.asyncio]


async def test_my_case(openai_api_key):
    rails = LLMRails(load_config(OPENAI_BASELINE_CONFIG), verbose=False)
    result = await rails.generate_async(prompt="...")
    assert result == snapshot()
```

Config constants (``OPENAI_BASELINE_CONFIG`` and friends) live in the suite-local
``configs.py`` next to the tests; ``snapshot`` is the suite-local re-export in
``tests/recorded/snapshots.py``, not ``inline_snapshot`` directly.

Request credentials as fixture parameters (`openai_api_key`, `nvidia_api_key`,
or `nvidia_inference_key`) rather than calling
`request.getfixturevalue(...)`. In modules that mix sync/async tests or
vcr/non-vcr tests, keep only `recorded` in `pytestmark` and apply
`@pytest.mark.vcr` / `@pytest.mark.asyncio` per test. Then use the Makefile
refresh workflow to record once with credentials, fill the snapshot offline,
and verify the replay:

```bash
OPENAI_API_KEY=... make record-cassettes \
  RECORDED_TESTS=path::test_my_case \
  RECORDED_REQUIRED_KEYS=OPENAI_API_KEY
```

## Negative paths

Record genuine provider errors when testing how an integration handles failed
calls. Invalid-model requests can provide refreshable error cases; the status
and error shape depend on the provider. Keep input-only validation and synthetic
transport failures in unit tests unless they need the recorded integration path.
Use a [fake cassette](#fake-outputs) only when the required response cannot be
reproduced or injected through a test double.

Co-locate error tests with their successful cases and name the failure and
expected behavior explicitly.

## Replay

```bash
uv run --locked pytest tests/recorded --block-network -v --durations=10
```

Focused rails replay:

```bash
uv run --locked pytest tests/recorded/rails/public_api --block-network -v
uv run --locked pytest tests/recorded/rails/library --block-network -v
```

Replay mode installs dummy API keys from `tests/recorded/utils.py`. A cassette miss with `--block-network` is a test failure.

## Refresh

Refresh only in a trusted environment with real provider credentials. The
record -> fill-snapshots -> verify loop is wrapped in make targets.

```bash
OPENAI_API_KEY=... NVIDIA_API_KEY=... make record-cassettes
```

`record-cassettes` defaults to `RECORDED_RECORD_MODE=once` and
`RECORDED_SNAPSHOT_MODE=create`, which records missing cassettes, fills empty
snapshots, and replays existing cassettes. This is the safest mode when adding
new tests because it will not rewrite unrelated existing cassettes selected by
the same path.

For a focused new cassette that only touches one provider, pass the test node and
override the preflight list:

```bash
OPENAI_API_KEY=... make record-cassettes \
  RECORDED_TESTS=tests/recorded/rails/public_api/test_generate.py::test_new_case \
  RECORDED_REQUIRED_KEYS=OPENAI_API_KEY
```

See the [proxy suite guide](server/experimental/README.md) for its endpoints,
dialect selection, credentials, and capture provenance.

`RECORDED_TESTS` is passed directly to pytest, so it can be a single test, a
test class, several files, or a directory:

```bash
OPENAI_API_KEY=... make record-cassettes \
  RECORDED_TESTS="tests/recorded/rails/public_api/test_generate.py tests/recorded/clients/test_openai_chat.py" \
  RECORDED_REQUIRED_KEYS=OPENAI_API_KEY
```

For an intentional rewrite of existing cassettes, use `rewrite-cassettes`:

```bash
OPENAI_API_KEY=... make rewrite-cassettes \
  RECORDED_TESTS=tests/recorded/rails/public_api/test_generate.py::test_openai_generate_async_public_contract \
  RECORDED_REQUIRED_KEYS=OPENAI_API_KEY
```

`rewrite-cassettes` uses `RECORDED_RECORD_MODE=rewrite` and
`RECORDED_SNAPSHOT_MODE=fix`, so changed recorded outputs update existing inline
snapshots before the final offline replay verification.

For a full trusted refresh, set the record mode explicitly:

```bash
OPENAI_API_KEY=... NVIDIA_API_KEY=... make record-cassettes RECORDED_RECORD_MODE=all RECORDED_SNAPSHOT_MODE=fix
```

Replay and snapshot-only workflows do not need real provider credentials:

```bash
make replay-cassettes RECORDED_TESTS=tests/recorded/rails/public_api/test_generate.py::test_openai_generate_async_public_contract
make snapshot-cassettes RECORDED_TESTS=tests/recorded/rails/public_api/test_generate.py::test_openai_generate_async_public_contract
```

## Cassettes

Use pytest-recording's default layout beside each test module:

```text
<suite>/cassettes/<test_module>/<test_name>.yaml
```

Parameterized tests include the parameter id in the filename. Do not add
`@pytest.mark.default_cassette(...)`.

JSON request and response bodies are stored as `parsed_body` and rehydrated by `ReadableYamlSerializer` during replay. SSE responses use `parsed_body` events only when the shared serializer can
rehydrate their framing faithfully. Other SSE bodies retain `string` storage,
including native Responses streams with named `event:` lines.

Cassettes preserve scrubbed JSON text without smart-character normalization so provider payloads stay inspectable. Request matching and snapshot helpers normalize smart quotes, dash variants, ellipses, and NFKC at comparison time. Response headers are dropped by exact name and by prefix (`x-`, `cf-`, `openai-`); `tests/recorded/sanitization.py` holds the `ALLOWED_HEADERS` exceptions that must survive the prefix sweep (currently `content-type` and `openai-version`).

Inspect a cassette:

```bash
uv run --locked python -m tests.recorded.inspect_cassette tests/recorded/rails/public_api/cassettes/test_stream/test_openai_stream_async_public_contract.yaml
```

## Snapshots

Rails replay outputs are pinned with inline snapshots after normalization. Create or fix snapshots with:

```bash
uv run --locked pytest tests/recorded/rails --block-network --inline-snapshot=create
uv run --locked pytest tests/recorded/rails --block-network --inline-snapshot=fix
uv run --locked pytest tests/recorded/rails --block-network --inline-snapshot=review
```

Snapshot formatting uses `ruff format` through `[tool.inline-snapshot]` in `pyproject.toml`.
Snapshot create/fix/review runs must be serial. Use `make record-cassettes` or a
direct `uv run --locked pytest ... --inline-snapshot=<mode>` command; the default
`make test` path uses xdist, where inline-snapshot disables update and report
modes.

Volatile response fields (ids, timestamps, fingerprints) are scrubbed to fixed sentinels in the cassette, so snapshots assert them directly without needing loose matchers.

## Fake Outputs

Prefer `FakeLLMModel` when a test needs the main model to emit a specific output and provider-backed rail/task calls can still replay from VCR. This keeps the test refreshable.

Use a fake cassette only when runtime injection cannot model the behavior clearly, such as a provider stream/error path. Fake cassettes must:

- live under a `cassettes/**/fake/` directory,
- use `@pytest.mark.fake_cassette`,
- be excluded from refresh with `-m "not fake_cassette"`,
- include YAML header metadata with `reason`, `frozen_fields`, and `fake_llm_model_considered`.

The fake-cassette metadata validator is in `tests/recorded/fake_cassettes.py`.
