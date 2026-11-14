from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..models import User
from ..security import require_role
from ..agents.bus import Message

router = APIRouter(prefix="/api/agents", tags=["agents"])


class InboxIn(BaseModel):
    conversation_id: str
    sender: str
    receiver: str
    performative: str
    content: dict


@router.post("/{agent_name}/inbox")
async def inbox(agent_name: str, body: InboxIn, request: Request,
                admin: User = Depends(require_role("admin"))):
    if agent_name not in request.app.state.agents:
        raise HTTPException(404, f"No agent '{agent_name}'")
    msg = Message(conversation_id=body.conversation_id, sender=body.sender,
                  receiver=body.receiver,
                  performative=body.performative, content=body.content)
    reply = await request.app.state.bus.send(msg)
    return {"ok": True, "reply": reply.model_dump(mode="json") if reply else None}