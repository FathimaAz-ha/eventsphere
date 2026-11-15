import uuid
from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional
from pydantic import BaseModel, Field

PERFORMATIVES = (
    "REQUEST", "AGREE", "REFUSE", "PROPOSE", "INFORM", "FAILURE",
    "CFP", "ACCEPT-PROPOSAL", "REJECT-PROPOSAL",
)


class Message(BaseModel):
    conversation_id: str
    sender: str
    receiver: str
    performative: str
    content: dict
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    protocol: str = "fipa-request"
    ontology: str = "eventsphere-ontology"
    language: str = "json"
    in_reply_to: Optional[str] = None
    reply_by: Optional[datetime] = None
    iteration: int = 1


# Standard FIPA ACL Message alias
FIPAMessage = Message


Handler = Callable[[Message], Awaitable[Message | None]]


def make(sender, receiver, performative, content, conversation_id=None,
         protocol="fipa-request", in_reply_to=None, iteration=1,
         ontology="eventsphere-ontology", language="json",
         reply_by=None) -> Message:
    assert performative in PERFORMATIVES, f"Invalid performative '{performative}'"
    return Message(
        conversation_id=conversation_id or uuid.uuid4().hex,
        sender=sender, receiver=receiver,
        performative=performative, content=content,
        protocol=protocol, in_reply_to=in_reply_to,
        iteration=iteration,
        ontology=ontology, language=language,
        reply_by=reply_by,
    )


class MessageBus:
    """In-process message bus. Agents communicate through standard FIPA ACL messages."""

    def __init__(self):
        self._handlers: dict[str, Handler] = {}
        self._log_writer = None

    def set_logger(self, fn):  # async fn(Message) -> None
        self._log_writer = fn

    def subscribe(self, name: str, handler: Handler):
        self._handlers[name] = handler

    def unsubscribe(self, name: str):
        self._handlers.pop(name, None)

    async def send(self, msg: Message) -> Message | None:
        if self._log_writer:
            await self._log_writer(msg)

        if msg.receiver == "*":
            # Broadcast to all subscribed agents (except sender)
            for name, h in self._handlers.items():
                if name == msg.sender:
                    continue
                copy = msg.model_copy(update={"receiver": name})
                await h(copy)
            return None

        handler = self._handlers.get(msg.receiver)
        if handler is None:
            failure = make(msg.receiver, msg.sender, "FAILURE",
                        {"reason": f"no agent named '{msg.receiver}'"},
                        msg.conversation_id, in_reply_to=msg.id)
            if self._log_writer:
                await self._log_writer(failure)
            return failure
        reply = await handler(msg)
        if reply and self._log_writer:
            await self._log_writer(reply)
        return reply

    async def broadcast_collect(self, msg: Message, target_prefix: str = "") -> list[Message]:
        """FIPA CNP: Broadcasts a CFP to matching participant agents and collects their proposals/refusals."""
        if self._log_writer:
            await self._log_writer(msg)

        replies = []
        for name, h in list(self._handlers.items()):
            if name == msg.sender:
                continue
            if target_prefix and not name.startswith(target_prefix):
                continue
            copy = msg.model_copy(update={"receiver": name})
            try:
                reply = await h(copy)
                if reply:
                    if self._log_writer:
                        await self._log_writer(reply)
                    replies.append(reply)
            except Exception:
                continue
        return replies