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

import json
from copy import deepcopy

import pytest

from nemoguardrails.server.experimental._content_checker import ContentInspectionPolicy, StreamBufferingPolicy
from nemoguardrails.server.experimental.provider.sse import iter_sse_events
from nemoguardrails.server.experimental.providers.openai.chat_completions.endpoint import CHAT_COMPLETIONS_ENDPOINT
from nemoguardrails.server.experimental.providers.openai.dialects.litellm.chat_completions import (
    LITELLM_CHAT_COMPLETIONS_ENDPOINT,
)
from tests.server.experimental.checkers import StaticChecker
from tests.server.experimental.conformance.openai.chat_completions.http_support import assert_output_error, proxy
from tests.server.experimental.conformance.openai.chat_completions.support import (
    object_properties,
    recorded_exchange,
    source_document,
    source_validator,
    sse_bytes,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.block_network(allowed_hosts=[r"^127\.0\.0\.1$", r"^::1$"])]
ENDPOINTS = [
    pytest.param(CHAT_COMPLETIONS_ENDPOINT, id="openai"),
    pytest.param(LITELLM_CHAT_COMPLETIONS_ENDPOINT, id="litellm"),
]
UNINSPECTED = "synthetic additional Chat content"


async def payloads(body):
    async def chunks():
        yield body

    return [
        json.loads(event.data)
        async for event in iter_sse_events(chunks(), max_event_bytes=1024 * 1024)
        if event.data not in (None, b"[DONE]")
    ]


async def exchange(request, body, endpoint, inspect):
    checker = StaticChecker(
        ContentInspectionPolicy(inspect, inspect, StreamBufferingPolicy(10000, 0) if inspect else None)
    )
    async with proxy(checker, body, streaming=request["stream"], endpoint=endpoint) as (client, sent, source):
        response = await client.post("/v1/chat/completions", json=request)
    if sent:
        assert len(sent) == 1
        assert json.loads(sent[0].content) == request
        assert source.closed
    return response, checker, sent


@pytest.mark.parametrize("gateway", [False, True], ids=["native-recording", "gateway-recording"])
@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_recorded_positive_controls_and_explicit_dialect_boundary(gateway, stream, inspect, endpoint):
    request, body = recorded_exchange(stream, gateway=gateway)
    response, checker, sent = await exchange(request, body, endpoint, inspect)
    assert len(sent) == 1
    if gateway and not stream and inspect and endpoint is CHAT_COMPLETIONS_ENDPOINT:
        assert_output_error(response, stream)
        assert checker.text_calls == [("input", request["messages"][0]["content"])]
        return
    if stream:
        text = "".join(
            choice.get("delta", {}).get("content") or "" for p in await payloads(body) for choice in p["choices"]
        )
    else:
        text = json.loads(body)["choices"][0]["message"]["content"]
    assert text
    assert response.status_code == 200
    assert response.content == body
    assert checker.text_calls == ([("input", request["messages"][0]["content"]), ("output", text)] if inspect else [])


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
async def test_native_observed_fields_are_covered_by_independent_source(stream):
    request, body = recorded_exchange(stream)
    source_validator("CreateChatCompletionRequest").validate(request)
    document = source_document()
    schemas = document["components"]["schemas"]
    root = "CreateChatCompletionStreamResponse" if stream else "CreateChatCompletionResponse"
    content = "ChatCompletionStreamResponseDelta" if stream else "ChatCompletionResponseMessage"
    root_fields = object_properties(document, schemas[root])
    choice_fields = object_properties(document, schemas[root]["properties"]["choices"]["items"])
    content_fields = object_properties(document, schemas[content])
    observed = await payloads(body) if stream else [json.loads(body)]
    for payload in observed:
        assert payload.keys() <= root_fields.keys()
        assert "provider_specific_fields" not in payload
        for choice in payload["choices"]:
            assert choice.keys() <= choice_fields.keys()
            node = choice["delta" if stream else "message"]
            assert node.keys() <= content_fields.keys()
    if stream:
        assert any("obfuscation" in p for p in observed)
        assert any(not p["choices"] and p.get("usage") for p in observed)
    else:
        assert observed[0]["choices"][0]["message"]["refusal"] is None
        assert observed[0]["choices"][0]["message"]["annotations"] == []


@pytest.mark.parametrize("gateway", [False, True], ids=["native-recording", "gateway-recording"])
@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize("probe", ["reasoning", "refusal", "case-alias", "logprobs", "extra-choice"])
async def test_recorded_content_mutations_cannot_become_metadata(gateway, stream, inspect, probe):
    request, body = recorded_exchange(stream, gateway=gateway)
    values = await payloads(body) if stream else [json.loads(body)]
    target = next(
        p for p in values if p["choices"] and p["choices"][0].get("delta" if stream else "message", {}).get("content")
    )
    choice = target["choices"][0]
    content = choice["delta" if stream else "message"]
    if probe == "reasoning":
        content["reasoning_content"] = UNINSPECTED
    elif probe == "refusal":
        content["refusal"] = UNINSPECTED
    elif probe == "case-alias":
        content["Content"] = UNINSPECTED
    elif probe == "logprobs":
        choice["logprobs"] = {"content": [{"token": UNINSPECTED}]}
    else:
        additional = deepcopy(choice)
        additional["index"] = 1
        additional["delta" if stream else "message"]["content"] = UNINSPECTED
        target["choices"].append(additional)
    mutated = sse_bytes(*values) if stream else json.dumps(values[0]).encode()
    endpoint = LITELLM_CHAT_COMPLETIONS_ENDPOINT if gateway else CHAT_COMPLETIONS_ENDPOINT
    response, checker, sent = await exchange(request, mutated, endpoint, inspect)
    assert len(sent) == 1
    if inspect:
        assert_output_error(response, stream)
        assert checker.text_calls == [("input", request["messages"][0]["content"])]
        assert UNINSPECTED not in response.text
    else:
        assert response.status_code == 200
        assert response.content == mutated
        assert checker.calls == []


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize("inside_usage", [False, True], ids=["unknown-content", "opaque-accounting"])
async def test_usage_only_chunk_retains_its_declared_metadata_boundary(endpoint, inspect, inside_usage):
    request, body = recorded_exchange(True)
    values = await payloads(body)
    usage = next(p for p in values if p["choices"] == [] and p.get("usage"))
    target = usage["usage"] if inside_usage else usage
    target["future_text"] = UNINSPECTED
    mutated = sse_bytes(*values)
    response, checker, sent = await exchange(request, mutated, endpoint, inspect)
    assert len(sent) == 1
    if inspect and not inside_usage:
        assert_output_error(response, True)
        assert checker.text_calls == [("input", request["messages"][0]["content"])]
    else:
        assert response.status_code == 200
        assert response.content == mutated
        text = "".join(c.get("delta", {}).get("content") or "" for p in values for c in p["choices"])
        assert checker.text_calls == (
            [("input", request["messages"][0]["content"]), ("output", text)] if inspect else []
        )


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
async def test_native_recordings_expose_exact_pinned_schema_discrepancies(stream):
    _, body = recorded_exchange(stream)
    observed = await payloads(body) if stream else [json.loads(body)]
    validator = source_validator("CreateChatCompletionStreamResponse" if stream else "CreateChatCompletionResponse")
    expected = {(("system_fingerprint",), "type")}
    if not stream:
        expected.add((("choices", 0), "required"))
    for payload in observed:
        errors = list(validator.iter_errors(payload))
        assert {(tuple(error.absolute_path), error.validator) for error in errors} == expected
        for error in errors:
            if error.validator == "required":
                assert set(error.validator_value) - error.instance.keys() == {"logprobs"}
            else:
                assert error.validator_value == "string"
                assert error.instance is None


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
@pytest.mark.parametrize("inspect", [False, True], ids=["relay", "inspected"])
@pytest.mark.parametrize(
    "updates",
    [
        {"messages": [{"role": "system", "content": UNINSPECTED}]},
        {"messages": [{"role": "user", "content": [{"type": "text", "text": UNINSPECTED}]}]},
        {"messages": [{"role": "user", "content": "question", "name": UNINSPECTED}]},
        {"tools": [{"type": "function", "function": {"name": "hidden", "description": UNINSPECTED}}]},
        {"prediction": {"type": "content", "content": UNINSPECTED}},
        {"logprobs": True},
        {"guardrails": []},
        {"fallbacks": []},
    ],
)
async def test_recorded_request_mutations_reject_uninspected_inputs_and_gateway_controls(
    endpoint, stream, inspect, updates
):
    request, body = recorded_exchange(stream)
    request.update(updates)
    response, checker, sent = await exchange(request, body, endpoint, inspect)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_chat_completions_shape"
    assert sent == []
    assert checker.calls == []
