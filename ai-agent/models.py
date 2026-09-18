from typing import List

from pydantic import BaseModel


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[Message]


class AgentTokensResponse(BaseModel):
    # actor_token is read from the agent's local file and is therefore always
    # available; obo_token is null when no OBO has been exchanged yet (the
    # agent only exchanges per tool call now) or in bypass mode.
    actor_token: str
    obo_token: str | None = None


class AssuranceResponse(BaseModel):
    # None when no tool call has completed yet for this subject, or when
    # user-mcp's backend has no assurance concept (e.g. the file backend).
    tool: str | None = None
    decision: str | None = None
    current_loa: int | None = None
    required_loa: int | None = None
