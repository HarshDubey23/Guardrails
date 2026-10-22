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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from nemoguardrails.server.experimental._provider_transport import ProviderTransport
from nemoguardrails.server.experimental.provider.integration import UpstreamSettings
from nemoguardrails.server.experimental.providers.openai.integration import create_openai_chat_router

BLOCKED_ERROR = {
    "error": {"message": "blocked by test", "type": "guardrails_violation", "param": None, "code": "content_blocked"}
}


@pytest.fixture(autouse=True)
def single_upstream_exchange(vcr):
    """Require one provider interaction per pass, including beneath dispatch."""
    yield
    assert len(vcr.requests) == 1
    if vcr.record_mode == "none":
        assert vcr.play_count == 1
        assert vcr.all_played


@asynccontextmanager
async def recorded_proxy(api_key, checker, dispatched=None, *, endpoint, base_url) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as upstream_client:
        dispatcher = ProviderTransport(
            UpstreamSettings(
                base_url=base_url,
                mount_path="/v1",
            ),
            upstream_client,
        )

        async def buffered_dispatch(request):
            if dispatched is not None:
                dispatched.append(request)
            return await dispatcher.dispatch(
                request, require_identity_response=checker.inspection_policy().inspect_output
            )

        async def streaming_dispatch(request):
            if dispatched is not None:
                dispatched.append(request)
            return await dispatcher.stream_dispatch(
                request, require_identity_response=checker.inspection_policy().inspect_output
            )

        app = FastAPI()
        app.include_router(
            create_openai_chat_router(
                endpoint=endpoint,
                checker=checker,
                dispatch=buffered_dispatch,
                stream_dispatch=streaming_dispatch,
            )
        )
        headers = {"authorization": f"Bearer {api_key}", "content-type": "application/json"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://recorded-proxy.test",
            headers=headers,
        ) as client:
            yield client


def stream_events(body):
    events = []
    for event in body.split(b"\n\n"):
        data = [line.removeprefix(b"data: ") for line in event.splitlines() if line.startswith(b"data: ")]
        if not data:
            continue
        payload = b"\n".join(data)
        events.append("[DONE]" if payload == b"[DONE]" else json.loads(payload))
    return events
