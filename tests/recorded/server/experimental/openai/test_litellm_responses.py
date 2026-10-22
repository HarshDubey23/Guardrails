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

"""Replay gateway Responses evidence through the actual bound policy and HTTP path.

Capture completes before policy validation, so a new unsupported shape is not
silently truncated or changed to fit the contract. Recording is followed by the
mandatory offline replay, where bound-policy and passthrough assertions run.
"""

import json
from functools import partial

import httpx
import pytest
from jsonschema import Draft202012Validator

from nemoguardrails.server.experimental._content_checker import (
    ContentAllowed,
    ContentBlocked,
    ContentInspectionPolicy,
    StreamBufferingPolicy,
)
from nemoguardrails.server.experimental.provider.contract_export import export_guard_contract
from nemoguardrails.server.experimental.provider.sse import iter_sse_events
from nemoguardrails.server.experimental.provider.stream import StreamEventRole
from nemoguardrails.server.experimental.providers.openai.dialects.litellm.responses import LITELLM_RESPONSES_ENDPOINT
from nemoguardrails.server.experimental.providers.openai.responses.endpoint import RESPONSES_ENDPOINT
from tests.recorded.cassette import cassette_request_jsons
from tests.server.experimental.checkers import StaticChecker
from tests.server.experimental.responses_proxy import responses_proxy

pytestmark = [
    pytest.mark.recorded,
    pytest.mark.vcr(ignore_hosts=["recorded-responses.test"]),
    pytest.mark.asyncio,
]

BASE_URL = "https://inference-api.nvidia.com/v1"
MODEL = "openai/openai/gpt-5-nano"
INPUT = "Reply with exactly: Hello."
LONG_INPUT = "Reply with exactly: A calm morning begins with warm sunlight, a quiet cup of tea, and a friendly hello."
LONG_STREAM_RECORDING = "test_success_preserves_policy_and_wire[streaming-long-output].yaml"
ENDPOINT = LITELLM_RESPONSES_ENDPOINT
proxy = partial(responses_proxy, endpoint=ENDPOINT, base_url=BASE_URL)


def responses_request(stream, *, model=MODEL, input_text=INPUT):
    return {
        "model": model,
        "input": input_text,
        "stream": stream,
        "store": False,
        "max_output_tokens": 256,
        "reasoning": {"effort": "minimal"},
    }


async def capture(api_key, request):
    async with httpx.AsyncClient(timeout=60) as client:
        return await client.post(
            BASE_URL + "/responses",
            json=request,
            headers={"authorization": f"Bearer {api_key}", "accept-encoding": "identity"},
        )


async def events(wire):
    async def chunks():
        yield wire

    return [event async for event in iter_sse_events(chunks(), max_event_bytes=1024 * 1024)]


async def validate_inventory(upstream, request):
    document = export_guard_contract(ENDPOINT)
    base_document = export_guard_contract(RESPONSES_ENDPOINT)
    ENDPOINT.guarded_request_model.validate_payload(request)
    Draft202012Validator(document["request"]).validate(request)
    if not request["stream"]:
        payload = upstream.json()
        assert payload.keys() <= base_document["response"]["properties"].keys()
        assert payload["access_programs"] is None
        assert payload["billing"] == {"payer": "developer"}
        assert payload["store"] is False
        assert payload["frequency_penalty"] == payload["presence_penalty"] == 0
        assert set(payload["tool_usage"]) == {"image_gen", "web_search"}
        parsed = ENDPOINT.guarded_response_model.validate_payload(payload)
        Draft202012Validator(document["response"]).validate(payload)
        return parsed.locate_guarded_message(payload).message.content

    assert ENDPOINT.stream is not None
    adapter = ENDPOINT.stream.create_adapter()
    validator = Draft202012Validator(document["stream"])
    text = []
    kinds = []
    for event in await events(upstream.content):
        if event.data not in (None, b"[DONE]"):
            payload = json.loads(event.data)
            validator.validate(payload)
            assert payload["model"] == MODEL
            assert "provider_specific_fields" not in payload
            if "response" in payload:
                assert payload["response"].keys() <= base_document["response"]["properties"].keys()
            kinds.append(payload["type"])
        classified = adapter.classify_event(event)
        if classified.role is StreamEventRole.GUARDED_TEXT:
            assert classified.text is not None
            text.append(classified.text)
    adapter.validate_end_of_stream()
    assert set(kinds) == {
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.output_item.done",
        "response.content_part.added",
        "response.content_part.done",
        "response.output_text.delta",
        "response.output_text.done",
        "response.completed",
    }
    assert kinds[-1] == "response.completed"
    return "".join(text)


@pytest.mark.parametrize(
    "stream,input_text",
    [
        pytest.param(False, INPUT, id="buffered"),
        pytest.param(True, INPUT, id="streaming"),
        pytest.param(True, LONG_INPUT, id="streaming-long-output"),
    ],
)
async def test_success_preserves_policy_and_wire(
    nvidia_inference_key, record_mode, recorded_cassette_path, vcr, stream, input_text
):
    request = responses_request(stream, input_text=input_text)
    upstream = await capture(nvidia_inference_key, request)
    assert upstream.status_code == 200
    if record_mode != "none":
        assert len(vcr.requests) == 1
        return

    assert vcr.play_count == 1
    assert vcr.all_played

    assert cassette_request_jsons(recorded_cassette_path) == [request]
    text = await validate_inventory(upstream, request)
    assert text
    if stream:
        text_parts = [
            payload["delta"]
            for event in await events(upstream.content)
            if event.data not in (None, b"[DONE]")
            and (payload := json.loads(event.data))["type"] == "response.output_text.delta"
            and payload["delta"]
        ]
        if input_text == LONG_INPUT:
            assert len(text_parts) >= 4
    else:
        message = next(item for item in upstream.json()["output"] if item["type"] == "message")
        text_parts = [part["text"] for part in message["content"] if part["type"] == "output_text"]
    assert "".join(text_parts) == text
    for inspected in (True, False):
        vcr.rewind()
        checker = StaticChecker(
            ContentInspectionPolicy(inspected, inspected, StreamBufferingPolicy(1, 0) if inspected else None)
        )
        dispatches = []
        async with proxy(nvidia_inference_key, checker, dispatches) as client:
            response = await client.post("/v1/responses", json=request)
        assert response.status_code == upstream.status_code
        assert response.content == upstream.content
        assert response.headers["content-type"] == upstream.headers["content-type"]
        assert len(dispatches) == len(vcr.requests) == vcr.play_count == 1
        assert vcr.all_played
        assert json.loads(dispatches[0].body) == request
        assert checker.text_calls == (
            [("input", input_text)] + [("output", part) for part in text_parts] if inspected else []
        )


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
async def test_invalid_model_preserves_provider_error(
    nvidia_inference_key, record_mode, recorded_cassette_path, vcr, stream
):
    request = responses_request(stream, model="openai/openai/recorded-invalid-model")
    upstream = await capture(nvidia_inference_key, request)
    assert upstream.status_code == 403
    assert upstream.json()["error"]["type"] == "key_model_access_denied"
    assert request["model"] in upstream.json()["error"]["message"]
    assert len(vcr.requests) == 1
    if record_mode != "none":
        return

    assert vcr.play_count == 1
    assert vcr.all_played
    assert cassette_request_jsons(recorded_cassette_path) == [request]
    for inspected in (True, False):
        vcr.rewind()
        checker = StaticChecker(
            ContentInspectionPolicy(inspected, inspected, StreamBufferingPolicy(1, 0) if inspected else None)
        )
        dispatches = []
        async with proxy(nvidia_inference_key, checker, dispatches) as client:
            response = await client.post("/v1/responses", json=request)
        assert len(dispatches) == len(vcr.requests) == vcr.play_count == 1
        assert vcr.all_played
        assert json.loads(dispatches[0].body) == request
        assert response.status_code == upstream.status_code
        assert response.content == upstream.content
        assert response.headers["content-type"] == upstream.headers["content-type"]
        assert checker.text_calls == ([("input", INPUT)] if inspected else [])


@pytest.mark.vcr("test_success_preserves_policy_and_wire[buffered].yaml", record_mode="none")
async def test_buffered_output_is_withheld_on_block(nvidia_inference_key, vcr):
    upstream = await capture(nvidia_inference_key, responses_request(False))
    assert len(vcr.requests) == vcr.play_count == 1
    assert vcr.all_played
    message = next(item for item in upstream.json()["output"] if item["type"] == "message")
    text = "".join(part["text"] for part in message["content"] if part["type"] == "output_text")
    assert text
    vcr.rewind()
    checker = StaticChecker(ContentInspectionPolicy(True, True), output_decisions=[ContentBlocked("blocked by test")])
    dispatches = []
    async with proxy(nvidia_inference_key, checker, dispatches) as client:
        response = await client.post("/v1/responses", json=responses_request(False))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "content_blocked"
    assert len(dispatches) == len(vcr.requests) == vcr.play_count == 1
    assert vcr.all_played
    assert checker.text_calls == [("input", INPUT), ("output", text)]
    assert text not in response.text


@pytest.mark.vcr(LONG_STREAM_RECORDING, record_mode="none")
@pytest.mark.parametrize("allowed_windows", [0, 1], ids=["first-window", "later-window"])
@pytest.mark.parametrize("chunk_size,context_size", [(1, 0), (2, 1)], ids=["single-delta", "overlapping-context"])
async def test_stream_releases_only_approved_prefix(
    nvidia_inference_key, vcr, allowed_windows, chunk_size, context_size
):
    request = responses_request(True, input_text=LONG_INPUT)
    upstream = await capture(nvidia_inference_key, request)
    recorded_events = await events(upstream.content)
    deltas = [
        (index, payload["delta"])
        for index, event in enumerate(recorded_events)
        if event.data not in (None, b"[DONE]")
        and (payload := json.loads(event.data))["type"] == "response.output_text.delta"
        and payload["delta"]
    ]
    assert len(deltas) >= (allowed_windows + 1) * chunk_size
    approved_deltas = allowed_windows * chunk_size
    released_end = deltas[approved_deltas - 1][0] + 1 if approved_deltas else 0
    assert len(vcr.requests) == vcr.play_count == 1
    assert vcr.all_played
    vcr.rewind()
    checker = StaticChecker(
        ContentInspectionPolicy(True, True, StreamBufferingPolicy(chunk_size, context_size)),
        output_decisions=[ContentAllowed()] * allowed_windows + [ContentBlocked("blocked by test")],
    )
    dispatches = []
    async with proxy(nvidia_inference_key, checker, dispatches) as client:
        response = await client.post("/v1/responses", json=request)
    assert response.status_code == 200
    received = await events(response.content)
    assert [event.raw for event in received[:-1]] == [event.raw for event in recorded_events[:released_end]]
    assert received[-1].event_type == b"error"
    assert json.loads(received[-1].data)["code"] == "content_blocked"
    assert b"[DONE]" not in response.content
    assert checker.text_calls == [("input", LONG_INPUT)] + [
        (
            "output",
            "".join(text for _, text in deltas[max(0, window * chunk_size - context_size) : (window + 1) * chunk_size]),
        )
        for window in range(allowed_windows + 1)
    ]
    assert len(dispatches) == len(vcr.requests) == vcr.play_count == 1
    assert vcr.all_played
