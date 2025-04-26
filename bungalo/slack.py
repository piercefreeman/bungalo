import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncGenerator, cast

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.async_listeners import AsyncSocketModeRequestListener
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient

from bungalo.logger import LOGGER


@dataclass
class SlackMessage:
    tid: str


class MessageQueue:
    """
    A simple queue interface for receiving Slack messages.
    Provides a blocking .next() method to get the next message.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def next(self, timeout: float | None = None) -> dict[str, Any]:
        """
        Get the next message from the queue.

        :raises asyncio.TimeoutError: If no message is received within the timeout
        :return: The next message
        """
        return await asyncio.wait_for(
            self.queue.get(),
            timeout=timeout,
        )

    def _put(self, message: dict[str, Any]) -> None:
        """Internal method to add messages to the queue."""
        self.queue.put_nowait(message)


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

    _web: AsyncWebClient

    def __init__(
        self,
        *,
        bot_token: str,
        app_token: str,
        channel_id: str,
    ):
        self.bot_token = bot_token
        self.app_token = app_token
        self.channel_id = channel_id

        self._web = AsyncWebClient(token=self.bot_token)

    async def create_status(
        self, text: str, parent_ts: SlackMessage | None = None
    ) -> SlackMessage:
        """
        Post a message, can be used either for status reporting or threading
        """
        resp = await self._web.chat_postMessage(
            channel=await self._get_channel_id(),
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
        await self._web.chat_update(
            channel=await self._get_channel_id(),
            ts=status_ts.tid,
            text=new_text,
        )

    @asynccontextmanager
    async def listen_for_replies(
        self,
        parent_ts: SlackMessage,
        *,
        user_filter: set[str] | None = None,
    ) -> AsyncGenerator[MessageQueue, None]:
        """
        Listen for replies in a thread.

        Usage:
        ```python
        async with client.listen_for_replies(thread_ts) as queue:
            message = await queue.next()
        ```

        :param parent_ts: Thread to listen to
        :param timeout: Maximum time to wait for each message
        :param user_filter: Optional set of user IDs to filter messages by
        :return: A MessageQueue that yields messages matching the criteria
        :raises asyncio.TimeoutError: If no message is received within the timeout
        """
        async with self.use_socket() as slack_socket:
            queue = MessageQueue()

            async def _push(client: SocketModeClient, req: SocketModeRequest) -> None:
                LOGGER.info(f"Received Slack event: {req.type} {req.payload}")
                if req.type != "events_api":
                    return
                evt = req.payload.get("event", {})
                if (
                    evt.get("type") == "message"
                    and evt.get("thread_ts") == parent_ts.tid
                    and evt.get("subtype") is None  # ignore edits, bot_msgs, etc.
                    and (user_filter is None or evt.get("user") in user_filter)
                ):
                    queue._put(evt)

            slack_socket.socket_mode_request_listeners.append(
                cast(AsyncSocketModeRequestListener, _push)
            )

            try:
                yield queue
            finally:
                slack_socket.socket_mode_request_listeners.remove(
                    cast(AsyncSocketModeRequestListener, _push)
                )

    @asynccontextmanager
    async def use_socket(self):
        slack_socket = SocketModeClient(
            app_token=self.app_token,
            web_client=self._web,
        )
        # ensure events are ACKed automatically
        slack_socket.socket_mode_request_listeners.append(
            cast(AsyncSocketModeRequestListener, self._auto_ack)
        )
        await slack_socket.connect()
        try:
            yield slack_socket
        finally:
            await slack_socket.close()

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

    async def _get_channel_id(self):
        return (
            self.channel_id
            if self.channel_id.startswith("C")
            else await self._channel_id_from_name(self.channel_id.lstrip("#"))
        )

    async def _channel_id_from_name(self, name: str) -> str:
        # requires channels:read
        responses = await self._web.conversations_list(limit=1000)
        channels = responses["channels"]
        if channels is None:
            raise ValueError("Failed to fetch channels")
        for ch in channels:
            if ch["name"] == name:
                return ch["id"]
        raise ValueError(f"Channel #{name} not found or bot not invited")
