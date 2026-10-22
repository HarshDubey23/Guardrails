# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Check LiteLLM Chat boundaries against recorded evidence and synthetic cases.

Recorded cases audit observed fields and bound-policy/export acceptance without
provider calls. Synthetic transport cases follow the reviewed source inventory;
they do not claim that recordings exercise every extension or that the OpenAI
source fixture validates LiteLLM's extensions.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from nemoguardrails.server.experimental.provider.contract_export import export_guard_contract
from nemoguardrails.server.experimental.provider.sse import ServerSentEvent
from nemoguardrails.server.experimental.provider.stream import StreamEventRole
from nemoguardrails.server.experimental.providers.openai.chat_completions.endpoint import CHAT_COMPLETIONS_ENDPOINT
from nemoguardrails.server.experimental.providers.openai.dialects.litellm.chat_completions import (
    LITELLM_CHAT_COMPLETIONS_ENDPOINT,
)
from tests.server.experimental.conformance.openai.chat_completions.http_support import (
    assert_output_error,
    checker_for,
    proxy,
)
from tests.server.experimental.conformance.openai.chat_completions.support import (
    chat_chunk,
    chat_request,
    chat_response,
    json_bytes,
    sse_bytes,
)

pytestmark = [
    pytest.mark.block_network(allowed_hosts=[r"^127\.0\.0\.1$", r"^::1$"]),
]
TEXT = "Cafe\u0301 — “quoted” ☃"
MESSAGE = ("choices", 0, "message")
CHOICE = ("choices", 0)
DELTA = ("choices", 0, "delta")
EXTENSION_LOCATIONS = [
    pytest.param(False, MESSAGE + ("provider_specific_fields",), id="message-bag"),
    pytest.param(False, CHOICE + ("provider_specific_fields",), id="choice-bag"),
    pytest.param(True, ("provider_specific_fields",), id="stream-root-bag"),
    pytest.param(True, DELTA + ("provider_specific_fields",), id="delta-bag"),
]
ARRAY_LOCATIONS = [
    pytest.param(streaming, location + (field,), id=f"{name}-{field}")
    for streaming, location, name, fields in [
        (False, MESSAGE, "message", ("images", "thinking_blocks", "reasoning_items")),
        (True, DELTA, "delta", ("images", "thinking_blocks", "reasoning_items", "annotations")),
    ]
    for field in fields
]


CASSETTES = Path(__file__).parents[6] / "recorded/server/experimental/openai/cassettes/test_litellm_chat_completions"
SUCCESS_RECORDINGS = (
    "test_success_preserves_policy_and_wire[buffered]",
    "test_alternate_prompt_preserves_policy_and_wire[buffered]",
    "test_success_preserves_policy_and_wire[streaming]",
    "test_alternate_prompt_preserves_policy_and_wire[streaming]",
)


def extension_values(value):
    """Find extension bags anywhere, including outside the inspected text path."""
    if isinstance(value, dict):
        for name, nested in value.items():
            if name == "provider_specific_fields":
                yield nested
            yield from extension_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from extension_values(nested)


@pytest.mark.parametrize("recording", SUCCESS_RECORDINGS)
def test_recorded_inventory_matches_bound_policy_and_export(recording):
    (interaction,) = yaml.safe_load((CASSETTES / f"{recording}.yaml").read_text())["interactions"]
    request = interaction["request"]["parsed_body"]
    response = interaction["response"]
    body = response["body"]["parsed_body"]
    original = deepcopy(body)
    endpoint = LITELLM_CHAT_COMPLETIONS_ENDPOINT
    document = export_guard_contract(endpoint)

    assert response["status"]["code"] == 200
    assert not {"caching", "num_retries", "guardrails", "fallbacks", "context_window_fallback_dict"} & request.keys()
    endpoint.guarded_request_model.validate_payload(request)
    Draft202012Validator(document["request"]).validate(request)

    if request["stream"]:
        assert list(extension_values(body)) == []
        assert endpoint.stream is not None
        adapter = endpoint.stream.create_adapter()
        validator = Draft202012Validator(document["stream"])
        text_events = 0
        for payload in body:
            if payload == "[DONE]":
                data = b"[DONE]"
            else:
                validator.validate(payload)
                data = json.dumps(payload).encode()
            event = ServerSentEvent.from_bytes(b"data: " + data + b"\n\n")
            classified = adapter.classify_event(event)
            if classified.role is StreamEventRole.GUARDED_TEXT:
                assert classified.text == payload["choices"][0]["delta"]["content"]
                text_events += 1
        adapter.validate_end_of_stream()
        assert text_events > 0
    else:
        assert len(list(extension_values(body))) == 2
        (choice,) = body["choices"]
        assert choice["message"]["provider_specific_fields"] == {"refusal": None}
        assert choice["provider_specific_fields"] == {}
        assert type(body["usage"]["completion_tokens_details"]["reasoning_tokens"]) is int
        endpoint.guarded_response_model.validate_payload(body)
        Draft202012Validator(document["response"]).validate(body)
    assert body == original


def extension_body(streaming, path, value):
    """Place one independently chosen extension beside the guarded text."""
    payload = chat_chunk(TEXT) if streaming else chat_response()
    if not streaming:
        payload["choices"][0]["message"]["content"] = TEXT
    target = payload
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    return sse_bytes(payload) if streaming else json_bytes(payload)


async def assert_extension_exchange(streaming, body, inspect, endpoint, accepted):
    checker = checker_for(inspect)
    request_body = json_bytes(chat_request(stream=streaming))
    async with proxy(checker, body, streaming=streaming, endpoint=endpoint) as (client, requests, source):
        response = await client.post(
            "/v1/chat/completions", content=request_body, headers={"content-type": "application/json"}
        )
    assert len(requests) == 1
    assert requests[0].content == request_body
    assert source.closed
    if not inspect or accepted:
        assert response.status_code == 200
        assert response.content == body
        assert checker.text_calls == ([("input", "question"), ("output", TEXT)] if inspect else [])
    else:
        assert_output_error(response, streaming)
        assert checker.text_calls == [("input", "question")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", [CHAT_COMPLETIONS_ENDPOINT, LITELLM_CHAT_COMPLETIONS_ENDPOINT], ids=["openai", "litellm"]
)
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize("streaming,path", EXTENSION_LOCATIONS)
@pytest.mark.parametrize("value", [None, {}], ids=["null", "empty"])
async def test_empty_extension_bags_require_explicit_dialect_selection(endpoint, inspect, streaming, path, value):
    await assert_extension_exchange(
        streaming,
        extension_body(streaming, path, value),
        inspect,
        endpoint,
        endpoint is LITELLM_CHAT_COMPLETIONS_ENDPOINT,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", [CHAT_COMPLETIONS_ENDPOINT, LITELLM_CHAT_COMPLETIONS_ENDPOINT], ids=["openai", "litellm"]
)
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize("streaming,path", ARRAY_LOCATIONS)
@pytest.mark.parametrize("value", [None, []], ids=["null", "empty"])
async def test_empty_content_extensions_do_not_add_inspection_targets(endpoint, inspect, streaming, path, value):
    await assert_extension_exchange(
        streaming,
        extension_body(streaming, path, value),
        inspect,
        endpoint,
        endpoint is LITELLM_CHAT_COMPLETIONS_ENDPOINT,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", [CHAT_COMPLETIONS_ENDPOINT, LITELLM_CHAT_COMPLETIONS_ENDPOINT], ids=["openai", "litellm"]
)
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize(
    "path,value",
    [
        pytest.param(
            MESSAGE + ("provider_specific_fields",), {"refusal": None, "thinking_blocks": []}, id="reviewed-message"
        ),
        pytest.param(
            CHOICE + ("provider_specific_fields",), {"native_finish_reason": "not guarded text"}, id="finish-metadata"
        ),
    ],
)
async def test_reviewed_buffered_members_preserve_bytes_without_changing_checked_text(endpoint, inspect, path, value):
    await assert_extension_exchange(
        False, extension_body(False, path, value), inspect, endpoint, endpoint is LITELLM_CHAT_COMPLETIONS_ENDPOINT
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize("streaming,path", ARRAY_LOCATIONS)
async def test_nonempty_content_extensions_are_withheld_when_inspected(inspect, streaming, path):
    await assert_extension_exchange(
        streaming,
        extension_body(streaming, path, ["unreviewed text"]),
        inspect,
        LITELLM_CHAT_COMPLETIONS_ENDPOINT,
        False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize("streaming,path", EXTENSION_LOCATIONS)
async def test_unreviewed_extension_members_fail_closed_at_every_container(inspect, streaming, path):
    await assert_extension_exchange(
        streaming,
        extension_body(streaming, path, {"future_content": "unreviewed text"}),
        inspect,
        LITELLM_CHAT_COMPLETIONS_ENDPOINT,
        False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize(
    "path,value",
    [
        pytest.param(DELTA + ("provider_specific_fields",), {"refusal": None}, id="buffered-refusal"),
        pytest.param(("provider_specific_fields",), {"native_finish_reason": "stop"}, id="buffered-finish-metadata"),
    ],
)
async def test_buffered_extension_permissions_do_not_leak_into_streams(inspect, path, value):
    await assert_extension_exchange(
        True, extension_body(True, path, value), inspect, LITELLM_CHAT_COMPLETIONS_ENDPOINT, False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize("extensions", [{}, {"future_content": "unreviewed text"}], ids=["reviewed", "unreviewed"])
async def test_usage_only_events_keep_metadata_role_without_bypassing_extension_validation(inspect, extensions):
    payload = chat_chunk(
        choices=[],
        usage={"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        provider_specific_fields=extensions,
    )
    body = sse_bytes(payload)
    checker = checker_for(inspect)
    async with proxy(checker, body, streaming=True, endpoint=LITELLM_CHAT_COMPLETIONS_ENDPOINT) as (
        client,
        requests,
        source,
    ):
        response = await client.post("/v1/chat/completions", json=chat_request(stream=True))
    assert len(requests) == 1
    assert source.closed
    assert checker.text_calls == ([("input", "question")] if inspect else [])
    if inspect and extensions:
        assert_output_error(response, True)
    else:
        assert response.status_code == 200
        assert response.content == body
