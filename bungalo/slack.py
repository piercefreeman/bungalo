import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncGenerator, cast

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.async_listeners import AsyncSocketModeRequestListener
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient


@dataclass
class SlackMessage:
    tid: str


@dataclass(slots=True, kw_only=True)
class SlackClient:
    """
    Async helper for long-running Slack interactions that need to:

    • start a new thread
    • listen for replies (without a public webhook)
    • post periodic updates to that thread
    • keep a single "status" message edited in-place (e.g. progress bar)

    The manager runs entirely over Socket Mode, so we don't need to separately
    expose a webhook over the public internet.

    """

    bot_token: str
    """
    xoxb-… token with `chat:write`, `channels:read`
    """

    app_token: str
    """
    xapp-… token with the `connections:write` scope
    """

    channel_id: str
    """
    destination channel (could be a DM id)
    """

    default_reply_timeout: float = 60.0

    _web: AsyncWebClient | None = None
    _socket: SocketModeClient | None = None
    _running: asyncio.Event = asyncio.Event()

    async def __aenter__(self) -> "SlackClient":
        self._web = AsyncWebClient(token=self.bot_token)
        self._socket = SocketModeClient(
            app_token=self.app_token,
            web_client=self._web,
        )
        # ensure events are ACKed automatically
        self._socket.socket_mode_request_listeners.append(
            cast(AsyncSocketModeRequestListener, self._auto_ack)
        )
        await self._socket.connect()
        self._running.set()
        return self

    async def __aexit__(self, *_exc):
        self._running.clear()
        if self._socket:
            await self._socket.close()

    async def create_status(
        self, text: str, parent_ts: SlackMessage | None = None
    ) -> SlackMessage:
        """
        Post a message, can be used either for status reporting or threading
        """
        assert self._web
        resp = await self._web.chat_postMessage(
            channel=self.channel_id,
            text=text,
            thread_ts=parent_ts.tid if parent_ts else None,
        )
        thread_id = resp["ts"]
        if not isinstance(thread_id, str):
            raise ValueError("Slack returned a non-string thread ID")
        return SlackMessage(tid=thread_id)

    async def update_status(self, status_ts: SlackMessage, new_text: str) -> None:
        """
        Replace the contents of the message identified by `status_ts`.
        Allows for updating status messages (e.g. progress 0 % → 100 %).
        """
        assert self._web
        await self._web.chat_update(
            channel=self.channel_id,
            ts=status_ts.tid,
            text=new_text,
        )

    async def post_periodic_updates(
        self,
        parent_ts: SlackMessage,
        messages: list[str],
        every: float = 10.0,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """
        Fire-and-forget coroutine that posts each `messages[i]` into the thread
        `parent_ts` every *`every`* seconds until either the list is exhausted
        or `cancel_event` is set.
        """
        assert self._web
        for txt in messages:
            if cancel_event and cancel_event.is_set():
                break
            await self._web.chat_postMessage(
                channel=self.channel_id,
                thread_ts=parent_ts.tid,
                text=txt,
            )
            try:
                await asyncio.wait_for(
                    cancel_event.wait() if cancel_event else asyncio.sleep(every),
                    timeout=every,
                )
            except asyncio.TimeoutError:
                # normal path – just sleep elapsed
                continue

    @asynccontextmanager
    async def listen_for_replies(
        self,
        parent_ts: SlackMessage,
        *,
        timeout: float | None = None,
        user_filter: set[str] | None = None,
    ) -> AsyncGenerator[AsyncGenerator[dict[str, Any], None], None]:
        """
        ```
        async with mgr.listen_for_replies(thread_ts) as replies:
            async for event in replies:
                ...
        ```
        Yields an *async generator* of raw Slack `message` events that match:
        • `thread_ts == parent_ts`
        • optional `user_filter`
        Stops when `timeout` elapses (relative) or the surrounding context exits.
        """
        assert self._socket
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _push(client: SocketModeClient, req: SocketModeRequest) -> None:
            if req.type != "events_api":
                return
            evt = req.payload.get("event", {})
            if (
                evt.get("type") == "message"
                and evt.get("thread_ts") == parent_ts.tid
                and evt.get("subtype") is None  # ignore edits, bot_msgs, etc.
                and (user_filter is None or evt.get("user") in user_filter)
            ):
                queue.put_nowait(evt)

        self._socket.socket_mode_request_listeners.append(
            cast(AsyncSocketModeRequestListener, _push)
        )

        async def _stream() -> AsyncGenerator[dict[str, Any], None]:
            while self._running.is_set():
                try:
                    nxt = await asyncio.wait_for(
                        queue.get(),
                        timeout=timeout or self.default_reply_timeout,
                    )
                except asyncio.TimeoutError:
                    break
                yield nxt

        try:
            yield _stream()
        finally:
            # remove the local listener
            self._socket.socket_mode_request_listeners.remove(
                cast(AsyncSocketModeRequestListener, _push)
            )

    @staticmethod
    async def _auto_ack(client: SocketModeClient, req: SocketModeRequest) -> None:
        """
        Slack *requires* every envelope-id be ACKed; ignoring this will disconnect
        the app after ~10 seconds.
        """
        if req.envelope_id:  # not every request type needs an ACK, but guard anyway
            await client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )
