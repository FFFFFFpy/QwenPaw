"""Concurrent bidirectional JSON-RPC-over-JSONL client."""

from __future__ import annotations

import asyncio
from collections import defaultdict
import inspect
import json
import logging
from typing import Any, Awaitable, Callable

from .errors import (
    CodexConnectionClosedError,
    CodexProtocolError,
    CodexRpcError,
)

logger = logging.getLogger(__name__)

NotificationHandler = Callable[[dict[str, Any]], Any]
ServerRequestHandler = Callable[
    [dict[str, Any]],
    dict[str, Any] | Awaitable[dict[str, Any]],
]
Unsubscribe = Callable[[], None]


class JsonRpcClient:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        max_message_bytes: int = 4 * 1024 * 1024,
        default_timeout: float = 30.0,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._max_message_bytes = max_message_bytes
        self._default_timeout = default_timeout
        self._write_lock = asyncio.Lock()
        self._request_id_lock = asyncio.Lock()
        self._next_request_id = 0
        self._pending: dict[int | str, asyncio.Future[dict[str, Any]]] = {}
        self._subscriptions: dict[
            str,
            list[tuple[NotificationHandler, str | None]],
        ] = defaultdict(list)
        self._server_handlers: dict[str, ServerRequestHandler] = {}
        self._server_tasks: set[asyncio.Task[None]] = set()
        self._reader_task: asyncio.Task[None] | None = None
        self._closed_error: BaseException | None = None

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def is_closed(self) -> bool:
        return self._closed_error is not None

    def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(
                self._read_loop(),
                name="codex-rpc-reader",
            )

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._closed_error is not None:
            raise self._closed_error
        async with self._request_id_lock:
            self._next_request_id += 1
            request_id = self._next_request_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            await self._write(payload)
            effective_timeout = timeout or self._default_timeout
            return await asyncio.wait_for(
                asyncio.shield(future),
                effective_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Codex request timed out: {method}") from None
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        await self._write(payload)

    def subscribe(
        self,
        method: str,
        handler: NotificationHandler,
        *,
        thread_id: str | None = None,
    ) -> Unsubscribe:
        entry = (handler, thread_id)
        self._subscriptions[method].append(entry)

        def unsubscribe() -> None:
            subscriptions = self._subscriptions.get(method)
            if subscriptions and entry in subscriptions:
                subscriptions.remove(entry)

        return unsubscribe

    def register_server_request(
        self,
        method: str,
        handler: ServerRequestHandler,
    ) -> None:
        self._server_handlers[method] = handler

    async def close(self) -> None:
        self._fail_all(CodexConnectionClosedError())
        for task in list(self._server_tasks):
            task.cancel()
        if self._server_tasks:
            await asyncio.gather(*self._server_tasks, return_exceptions=True)
        if (
            self._reader_task is not None
            and self._reader_task is not asyncio.current_task()
        ):
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)

    async def _write(self, message: dict[str, Any]) -> None:
        if self._closed_error is not None:
            raise self._closed_error
        encoded = json.dumps(message, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self._max_message_bytes:
            raise CodexProtocolError("Codex JSON-RPC message is too large")
        async with self._write_lock:
            try:
                self._writer.write(encoded + b"\n")
                await self._writer.drain()
            except (BrokenPipeError, ConnectionError) as exc:
                closed = CodexConnectionClosedError()
                self._fail_all(closed)
                raise closed from exc

    async def _read_loop(self) -> None:
        try:
            while True:
                try:
                    line = await self._reader.readline()
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    raise CodexProtocolError(
                        "Codex JSON-RPC line exceeds the configured limit",
                    ) from exc
                if not line:
                    raise CodexConnectionClosedError()
                if len(line) > self._max_message_bytes:
                    raise CodexProtocolError(
                        "Codex JSON-RPC line exceeds the configured limit",
                    )
                try:
                    message = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CodexProtocolError(
                        "Codex App Server emitted malformed JSON",
                    ) from exc
                if not isinstance(message, dict):
                    raise CodexProtocolError(
                        "Codex App Server emitted a non-object message",
                    )
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except (CodexProtocolError, CodexConnectionClosedError) as exc:
            self._fail_all(exc)

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            future = self._pending.get(message["id"])
            if future is None or future.done():
                logger.debug(
                    "Ignoring duplicate or unknown Codex RPC response"
                )
                return
            if "error" in message:
                error = message.get("error")
                rpc_code = -32000
                error_message = "Codex App Server request failed"
                if isinstance(error, dict):
                    if isinstance(error.get("code"), int):
                        rpc_code = error["code"]
                    if isinstance(error.get("message"), str):
                        error_message = error["message"]
                future.set_exception(CodexRpcError(rpc_code, error_message))
            else:
                result = message.get("result")
                future.set_result(result if isinstance(result, dict) else {})
            return

        method = message.get("method")
        if not isinstance(method, str):
            logger.debug("Ignoring Codex message without a method")
            return
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        if "id" in message:
            task = asyncio.create_task(
                self._handle_server_request(message["id"], method, params),
                name=f"codex-server-request-{method}",
            )
            self._server_tasks.add(task)
            task.add_done_callback(self._server_task_done)
            return

        for handler, thread_id in list(self._subscriptions.get(method, [])):
            if thread_id is not None and params.get("threadId") != thread_id:
                continue
            try:
                result = handler(params)
                if inspect.isawaitable(result):

                    async def await_handler() -> None:
                        await result

                    task = asyncio.create_task(await_handler())
                    task.add_done_callback(self._background_task_done)
            except Exception:
                logger.warning("Codex notification handler failed: %s", method)

    async def _handle_server_request(
        self,
        request_id: int | str,
        method: str,
        params: dict[str, Any],
    ) -> None:
        handler = self._server_handlers.get(method)
        if handler is None:
            await self._write(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": "Method not supported by QwenPaw",
                    },
                },
            )
            return
        try:
            result = handler(params)
            if inspect.isawaitable(result):
                result = await result
            await self._write({"id": request_id, "result": result or {}})
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._write(
                {
                    "id": request_id,
                    "error": {
                        "code": -32001,
                        "message": "QwenPaw rejected the server request",
                    },
                },
            )

    def _fail_all(self, error: BaseException) -> None:
        if self._closed_error is None:
            self._closed_error = error
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    def _server_task_done(self, task: asyncio.Task[None]) -> None:
        self._server_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _background_task_done(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()
