from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any

import httpx

from commands.context import CommandContext
from packages.contracts.service_access import (
    SERVICE_REQUEST_MAX_BODY_BYTES,
    SERVICE_REQUEST_SAFE_RESPONSE_HEADERS,
    SERVICE_REQUEST_TIMEOUT_SECONDS,
    ServiceHttpRequestCommandPayload,
    ServiceRequestResult,
)


class ServiceAccessExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ServiceRequestCancelled(RuntimeError):
    pass


async def execute_service_http_request(
    ctx: CommandContext[ServiceHttpRequestCommandPayload],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ServiceRequestResult:
    resource = ctx.payload.resource
    service = await ctx.kubernetes.get_namespaced_resource(
        api_group="core",
        version="v1",
        namespace=resource.namespace or "",
        resource="services",
        name=resource.name,
    )
    _validate_exact_service(service, ctx.payload)
    cancel_requested = ctx.metadata.get("cooperative_cancel_requested")
    if isinstance(cancel_requested, asyncio.Event) and cancel_requested.is_set():
        raise ServiceRequestCancelled("service request cancelled before network access")

    started = time.monotonic()
    request_task = asyncio.create_task(
        _request_service(ctx.payload, transport=transport),
    )
    cancel_task: asyncio.Task[bool] | None = None
    if isinstance(cancel_requested, asyncio.Event):
        cancel_task = asyncio.create_task(cancel_requested.wait())
    try:
        if cancel_task is None:
            response = await request_task
        else:
            done, _pending = await asyncio.wait(
                {request_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done and cancel_requested.is_set():
                request_task.cancel()
                with suppress(asyncio.CancelledError):
                    await request_task
                raise ServiceRequestCancelled("service request cancelled")
            response = await request_task
    finally:
        if cancel_task is not None:
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task

    body = _bounded_text(response["body"])
    return ServiceRequestResult(
        status=int(response["status"]),
        status_text=str(response["status_text"]),
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        headers=dict(response["headers"]),
        body=body,
        truncated=bool(response["truncated"]),
        body_bytes=len(body.encode("utf-8")),
        error=None,
    )


async def _request_service(
    payload: ServiceHttpRequestCommandPayload,
    *,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    resource = payload.resource
    url = (
        f"{payload.scheme}://{resource.name}.{resource.namespace}.svc:{payload.port}{payload.path}"
    )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(SERVICE_REQUEST_TIMEOUT_SECONDS),
            follow_redirects=False,
            transport=transport,
        ) as client:
            async with client.stream(
                "GET",
                url,
                headers={"accept": "*/*", "user-agent": "opsia-cluster-agent/service-access"},
            ) as response:
                body = bytearray()
                truncated = False
                async for chunk in response.aiter_bytes():
                    remaining = SERVICE_REQUEST_MAX_BODY_BYTES - len(body)
                    if remaining <= 0:
                        truncated = True
                        break
                    body.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated = True
                        break
                headers = {
                    name.casefold(): value
                    for name, value in response.headers.items()
                    if name.casefold() in SERVICE_REQUEST_SAFE_RESPONSE_HEADERS
                }
                return {
                    "status": response.status_code,
                    "status_text": response.reason_phrase,
                    "headers": headers,
                    "body": bytes(body),
                    "truncated": truncated,
                }
    except httpx.TimeoutException as error:
        raise ServiceAccessExecutionError(
            "service_request_timeout",
            "service request timed out",
        ) from error
    except httpx.HTTPError as error:
        raise ServiceAccessExecutionError(
            "service_request_transport_error",
            "service request transport failed",
        ) from error


def _validate_exact_service(
    service: dict[str, Any],
    payload: ServiceHttpRequestCommandPayload,
) -> None:
    resource = payload.resource
    metadata = service.get("metadata")
    spec = service.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise ServiceAccessExecutionError(
            "service_identity_unavailable",
            "service identity is unavailable",
        )
    identity = (
        str(service.get("apiVersion") or ""),
        str(service.get("kind") or "").casefold(),
        str(metadata.get("namespace") or ""),
        str(metadata.get("name") or ""),
        str(metadata.get("uid") or ""),
    )
    expected = (
        "v1",
        "service",
        resource.namespace or "",
        resource.name,
        resource.uid,
    )
    if identity != expected:
        raise ServiceAccessExecutionError(
            "service_identity_changed",
            "service identity changed after request acceptance",
        )
    if str(spec.get("type") or "").casefold() == "externalname":
        raise ServiceAccessExecutionError(
            "service_external_name_forbidden",
            "ExternalName services are not eligible for in-cluster requests",
        )
    ports = spec.get("ports")
    if not isinstance(ports, list) or not any(
        isinstance(item, dict)
        and item.get("port") == payload.port
        and str(item.get("protocol") or "TCP").upper() == "TCP"
        for item in ports
    ):
        raise ServiceAccessExecutionError(
            "service_port_changed",
            "service port is no longer available",
        )


def _bounded_text(value: object) -> str:
    raw = value if isinstance(value, bytes) else bytes(value or b"")
    text = raw.decode("utf-8", errors="replace")
    while len(text.encode("utf-8")) > SERVICE_REQUEST_MAX_BODY_BYTES:
        text = text[:-1]
    return text
