from httpx import AsyncClient


class SlackClient:
    def __init__(self, token: str):
        self.token = token

    async def send_message(self, message: str):
        async with AsyncClient() as client:
            await client.post(
                self.token,
                json={
                    "text": f"<!channel> {message}",
                },
                timeout=5,
            )
