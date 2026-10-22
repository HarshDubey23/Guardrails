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

"""Replay observed Chat exchanges with synthetic, provider-neutral verdicts.

This suite exercises protocol integration, not real Rails configuration or
socket delivery. Blocking cases reuse successful recordings without rewriting
them or recording safety-service decisions.
"""

import json
from functools import partial

import httpx
import pytest

from nemoguardrails.server.experimental._content_checker import (
    ContentAllowed,
    ContentBlocked,
    ContentInspectionPolicy,
    StreamBufferingPolicy,
)
from nemoguardrails.server.experimental.providers.openai.chat_completions.endpoint import CHAT_COMPLETIONS_ENDPOINT
from tests.recorded.cassette import cassette_request_jsons
from tests.recorded.server.experimental.openai.chat_support import BLOCKED_ERROR, raw_stream_events, stream_events
from tests.recorded.server.experimental.openai.chat_support import recorded_proxy as chat_proxy
from tests.recorded.server.experimental.openai.chat_support import single_upstream_exchange as single_upstream_exchange
from tests.server.experimental.checkers import StaticChecker

pytestmark = [
    pytest.mark.recorded,
    pytest.mark.vcr(ignore_hosts=["recorded-proxy.test"]),
    pytest.mark.asyncio,
]

NATIVE_BASE_URL = "https://api.openai.com/v1"
NATIVE_MODEL = "gpt-5-nano-2025-08-07"
NATIVE_INPUT = "Reply with exactly: Hello, recorded Chat!"
NATIVE_BUFFERED_RECORDING = "test_success_preserves_policy_and_wire[buffered].yaml"
NATIVE_STREAMING_RECORDING = "test_success_preserves_policy_and_wire[streaming].yaml"

recorded_proxy = partial(chat_proxy, endpoint=CHAT_COMPLETIONS_ENDPOINT, base_url=NATIVE_BASE_URL)


def native_request(stream, *, model=NATIVE_MODEL):
    request = {
        "model": model,
        "messages": [{"role": "user", "content": NATIVE_INPUT}],
        "max_completion_tokens": 256,
        "reasoning_effort": "minimal",
        "store": False,
        "n": 1,
        "stream": stream,
    }
    if stream:
        request["stream_options"] = {"include_usage": True}
    return request


async def capture_native(api_key, request):
    async with httpx.AsyncClient(timeout=60) as client:
        return await client.post(
            NATIVE_BASE_URL + "/chat/completions",
            json=request,
            headers={"authorization": f"Bearer {api_key}", "accept-encoding": "identity"},
        )


def assert_single_replay(vcr):
    assert len(vcr.requests) == vcr.play_count == 1
    assert vcr.all_played


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
async def test_success_preserves_policy_and_wire(openai_api_key, record_mode, recorded_cassette_path, vcr, stream):
    request = native_request(stream)
    upstream = await capture_native(openai_api_key, request)
    assert upstream.status_code == 200
    if record_mode != "none":
        return
    assert_single_replay(vcr)
    assert cassette_request_jsons(recorded_cassette_path) == [request]
    if stream:
        events = stream_events(upstream.content)
        assert events[-1] == "[DONE]"
        payloads = [event for event in events if isinstance(event, dict)]
        text_parts = [
            choice["delta"]["content"]
            for payload in payloads
            for choice in payload["choices"]
            if choice.get("delta", {}).get("content")
        ]
        assert any(payload["choices"] == [] and payload.get("usage") for payload in payloads)
    else:
        payload = upstream.json()
        assert len(payload["choices"]) == 1
        assert payload["choices"][0]["message"]["role"] == "assistant"
        text_parts = [payload["choices"][0]["message"]["content"]]
    assert text_parts and all(text_parts)
    for inspected in (True, False):
        vcr.rewind()
        checker = StaticChecker(
            ContentInspectionPolicy(inspected, inspected, StreamBufferingPolicy(1, 0) if inspected else None)
        )
        dispatched = []
        async with recorded_proxy(openai_api_key, checker, dispatched) as client:
            response = await client.post("/v1/chat/completions", json=request)
        assert_single_replay(vcr)
        assert len(dispatched) == 1
        assert json.loads(dispatched[0].body) == request
        assert response.status_code == 200
        assert response.content == upstream.content
        assert response.headers["content-type"] == upstream.headers["content-type"]
        assert checker.text_calls == (
            [("input", NATIVE_INPUT)] + [("output", part) for part in text_parts] if inspected else []
        )


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
async def test_invalid_model_preserves_provider_error(openai_api_key, record_mode, recorded_cassette_path, vcr, stream):
    request = native_request(stream, model="recorded-invalid-chat-model")
    upstream = await capture_native(openai_api_key, request)
    assert upstream.status_code == 404
    assert upstream.json()["error"]["code"] == "model_not_found"
    if record_mode != "none":
        return
    assert_single_replay(vcr)
    assert cassette_request_jsons(recorded_cassette_path) == [request]
    vcr.rewind()
    checker = StaticChecker(ContentInspectionPolicy(True, True, StreamBufferingPolicy(1, 0)))
    dispatched = []
    async with recorded_proxy(openai_api_key, checker, dispatched) as client:
        response = await client.post("/v1/chat/completions", json=request)
    assert_single_replay(vcr)
    assert len(dispatched) == 1
    assert response.status_code == upstream.status_code
    assert response.content == upstream.content
    assert checker.text_calls == [("input", NATIVE_INPUT)]


@pytest.mark.vcr(NATIVE_BUFFERED_RECORDING, record_mode="none")
async def test_buffered_output_is_withheld_on_block(openai_api_key, vcr):
    upstream = await capture_native(openai_api_key, native_request(False))
    assert_single_replay(vcr)
    text = upstream.json()["choices"][0]["message"]["content"]
    vcr.rewind()
    checker = StaticChecker(ContentInspectionPolicy(True, True), output_decisions=[ContentBlocked("blocked by test")])
    dispatched = []
    async with recorded_proxy(openai_api_key, checker, dispatched) as client:
        response = await client.post("/v1/chat/completions", json=native_request(False))
    assert_single_replay(vcr)
    assert len(dispatched) == 1
    assert response.status_code == 400
    assert response.json() == BLOCKED_ERROR
    assert text not in response.text
    assert checker.text_calls == [("input", NATIVE_INPUT), ("output", text)]


@pytest.mark.vcr(NATIVE_STREAMING_RECORDING, record_mode="none")
@pytest.mark.parametrize("allowed_windows", [0, 1], ids=["first-window", "later-window"])
@pytest.mark.parametrize("chunk_size,context_size", [(1, 0), (2, 1)], ids=["single-delta", "overlapping-context"])
async def test_stream_releases_only_approved_prefix(openai_api_key, vcr, allowed_windows, chunk_size, context_size):
    upstream = await capture_native(openai_api_key, native_request(True))
    assert_single_replay(vcr)
    events = await raw_stream_events(upstream.content)
    text_events = [
        (index, choice["delta"]["content"])
        for index, event in enumerate(events)
        if event.data not in (None, b"[DONE]")
        for choice in json.loads(event.data)["choices"]
        if choice.get("delta", {}).get("content")
    ]
    assert len(text_events) >= (allowed_windows + 1) * chunk_size
    approved = allowed_windows * chunk_size
    released_end = text_events[approved - 1][0] + 1 if approved else 0
    vcr.rewind()
    checker = StaticChecker(
        ContentInspectionPolicy(True, True, StreamBufferingPolicy(chunk_size, context_size)),
        output_decisions=[ContentAllowed()] * allowed_windows + [ContentBlocked("blocked by test")],
    )
    dispatched = []
    async with recorded_proxy(openai_api_key, checker, dispatched) as client:
        response = await client.post("/v1/chat/completions", json=native_request(True))
    assert_single_replay(vcr)
    assert len(dispatched) == 1
    assert response.status_code == 200
    received = await raw_stream_events(response.content)
    assert [event.raw for event in received[:-2]] == [event.raw for event in events[:released_end]]
    assert json.loads(received[-2].data) == BLOCKED_ERROR
    assert received[-1].data == b"[DONE]"
    assert checker.text_calls == [("input", NATIVE_INPUT)] + [
        (
            "output",
            "".join(
                text for _, text in text_events[max(0, window * chunk_size - context_size) : (window + 1) * chunk_size]
            ),
        )
        for window in range(allowed_windows + 1)
    ]
