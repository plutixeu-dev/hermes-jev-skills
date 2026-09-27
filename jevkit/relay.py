"""What an agent gets from the conversation, and how its answer comes back.

Out: a handoff in the reasoning library's shape (To, Reason, Request, Constraints, Evidence,
Tried, Need back) and the last few turns, text only. System prompts and tool output never
leave: that is where memory, files and credentials live. For an agent off this machine every
line is redacted, and a turn that looks like it holds a secret stops the handoff: None.

Back: the agent's answer unchanged, under one line that says who wrote it. The chat model does
not retell it, so no number, warning or decision can change on the way.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import privacy

_IMAGE_PARTS = ("image_url", "input_image", "image")


def text_of(content: Any) -> str:
    """The text of one message: a string, or the text parts of a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, dict) and part.get("type") in _IMAGE_PARTS:
                parts.append("[image]")
        return "\n".join(part for part in parts if part)
    return ""


def has_images(messages: Sequence[Dict[str, Any]]) -> bool:
    """Does the newest user message carry an image. A handed-off turn is text only today."""
    for message in reversed(list(messages)):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            return isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") in _IMAGE_PARTS for part in content)
    return False


def _turns(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The user and assistant messages that have text: all a handoff can ever carry."""
    return [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            and text_of(m.get("content")).strip()]


def leaving_text(messages: Sequence[Dict[str, Any]], *, request: Optional[str] = None,
                 max_messages: int = 6) -> str:
    """Everything a handoff of these messages would carry, as one text: what the privacy check reads.

    Checking only the newest message let an earlier one about a client file leave as history.
    """
    turns = _turns(messages)
    if not turns or turns[-1].get("role") != "user":
        return request or ""
    asked = request if request is not None else text_of(turns[-1]["content"])
    earlier = turns[-(max_messages + 1):-1] if max_messages > 0 else []
    return "\n".join([asked] + [text_of(m.get("content")) for m in earlier])


def build_handoff(messages: Sequence[Dict[str, Any]], *, agent: str, reason: str, request: Optional[str] = None,
                  external: bool = True, max_messages: int = 6, max_chars: int = 12000) -> Optional[str]:
    """The task text for one agent, or None when nothing may be sent.

    `request`, when given, is the person's own words for this turn: what plugins appended to the
    user message (a skill suggestion, a handoff capsule) is then left behind.
    """
    turns = _turns(messages)
    if not turns or turns[-1].get("role") != "user":
        return None
    asked = (request if request is not None else text_of(turns[-1]["content"])).strip()
    earlier = turns[-(max_messages + 1):-1] if max_messages > 0 else []
    if external and (privacy.has_secret_value(asked)
                     or any(privacy.has_secret_value(text_of(m.get("content"))) for m in [turns[-1], *earlier])):
        return None
    budget = max(200, max_chars // 2)
    each = max(200, (max_chars - budget) // len(earlier)) if earlier else 0

    def clean(text: str, limit: int) -> str:
        if external:
            return privacy.redact(text, limit)
        return text if len(text) <= limit else text[:limit] + " […]"

    lines = ["<handoff>", f"To: {agent}", f"Reason: {reason}", f"Request: {clean(asked, budget)}",
             "Constraints: answer in writing only; change no files and no systems; "
             "answer in the language of the request",
             "Evidence: " + ("the recent conversation below" if earlier else "nothing beyond the request"),
             "Tried: nothing yet",
             "Need back: a complete answer the person can read as it is",
             "</handoff>"]
    if earlier:
        lines += ["", "Recent conversation, oldest first:"]
        lines += [f"[{m['role']}] {clean(text_of(m.get('content')).strip(), each)}" for m in earlier]
    return "\n".join(lines)


def relay(answer: str, *, agent: str, model: str = "") -> str:
    """The agent's answer as the person sees it: unchanged, with who wrote it on the first line."""
    who = f"{agent} · {model}" if model else agent
    return f"[{who}]\n\n{answer.strip()}"
