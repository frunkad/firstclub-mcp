"""User prompts (pickers/forms) that work on every MCP protocol era.

- 2026-07-28 clients: a tool can't push a prompt mid-call. Instead it returns an `InputRequiredResult`
  listing what to ask; the client shows it and calls the tool again with the answers. The tool re-runs
  from the top, so earlier answers (and "already done" markers for side effects) travel in
  `request_state`, and each ask() returns its stored answer on replay.
- Older clients: ask() falls back to classic `ctx.elicit()`.
- Clients without prompts at all (no elicitation capability): ask() raises PromptsUnsupported and callers
  use their "ask in chat" fallbacks. Any other failure (timeout, transport, bad answer) propagates, so
  callers never treat it as consent.
"""
from __future__ import annotations

import json
from typing import Any

import mcp_types
from fastmcp import Context
from fastmcp.server.elicitation import (
    CancelledElicitation,
    DeclinedElicitation,
    handle_elicit_accept,
    parse_elicit_response_type,
)


class PromptsUnsupported(Exception):
    """The client doesn't advertise elicitation, so it can't show pickers/forms at all."""


class NeedInput(Exception):
    """Raised by ask() on 2026-07-28 connections; guard() turns it into an InputRequiredResult."""

    def __init__(self, result: mcp_types.InputRequiredResult):
        super().__init__("input required")
        self.result = result


class Asker:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.answers: dict[str, dict] = {}  # key -> {"action", "content"}
        self.memo: dict[str, Any] = {}      # side effects already done in earlier rounds
        state = getattr(ctx, "request_state", None)
        if state:
            try:
                d = json.loads(state)
                self.answers.update(d.get("answers") or {})
                self.memo.update(d.get("memo") or {})
            except ValueError:
                pass
        for k, v in dict(getattr(ctx, "input_responses", None) or {}).items():
            self.answers[k] = {"action": getattr(v, "action", "cancel"), "content": getattr(v, "content", None)}
        try:
            self.modern = bool(ctx._is_modern_protocol())
        except Exception:
            self.modern = False

    @property
    def can_prompt(self) -> bool:
        """Whether the client advertises elicitation (needed for pickers/forms in either protocol era)."""
        try:
            caps = self.ctx.session.client_capabilities
        except Exception:
            return False
        return bool(caps is not None and getattr(caps, "elicitation", None) is not None)

    async def ask(self, key: str, message: str, response_type: Any, title: str | None = None):
        kw = {"response_title": title} if title else {}
        cfg = parse_elicit_response_type(response_type, **kw)
        if key in self.answers:  # answered in an earlier round
            a = self.answers[key]
            if a.get("action") == "accept":
                return handle_elicit_accept(cfg, a.get("content"))
            return DeclinedElicitation() if a.get("action") == "decline" else CancelledElicitation()
        if not self.can_prompt:
            raise PromptsUnsupported(key)
        if self.modern:
            raise NeedInput(mcp_types.InputRequiredResult(
                input_requests={key: mcp_types.ElicitRequest(params=mcp_types.ElicitRequestFormParams(
                    message=message, requested_schema=cfg.schema))},
                request_state=json.dumps({"answers": self.answers, "memo": self.memo}),
            ))
        return await self.ctx.elicit(message, response_type, **kw)  # errors/timeouts propagate: callers fail closed
