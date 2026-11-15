import uuid
from datetime import datetime, timezone
from typing import Callable, Awaitable
from pydantic import BaseModel, Field

PERFORMATIVES = ("REQUEST", "AGREE", "REFUSE", "PROPOSE", "INFORM", "FAILURE")


class Message(BaseModel):
    conversation_id: str
    sender: str
    receiver: str
    performative: str
    content: dict
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


Handler = Callable[[Message], Awaitable[Message | None]]


def make(sender, receiver, performative, content, conversation_id=None) -> Message:
    assert performative in PERFORMATIVES, performative
    return Message(
        conversation_id=conversation_id or uuid.uuid4().hex,
        sender=sender, receiver=receiver,
        performative=performative, content=content,
    )


class MessageBus:
    """In-process message bus. Agents only ever touch this."""

    def __init__(self):
        self._handlers: dict[str, Handler] = {}
        self._log_writer = None

    def set_logger(self, fn):        # async fn(Message) -> None
        self._log_writer = fn

    def subscribe(self, name: str, handler: Handler):
        self._handlers[name] = handler

    async def send(self, msg: Message) -> Message | None:
        if self._log_writer:
            await self._log_writer(msg)

        if msg.receiver == "*": 
                               # broadcast
            for name, h in self._handlers.items():
                if name == msg.sender :
                    continue
               
                copy = msg.model_copy(update={"receiver": name})
                await h(copy)
            return None

        handler = self._handlers.get(msg.receiver)
        if handler is None:
            return make(msg.receiver, msg.sender, "FAILURE",
                        {"reason": f"no agent named '{msg.receiver}'"},
                        msg.conversation_id)
        return await handler(msg)