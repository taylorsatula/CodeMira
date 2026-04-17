from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Literal


USER_PREFIX = "User:"
ASSISTANT_PREFIX = "Assistant:"
TOOL_PREFIX = "Tool:"


@dataclass
class Turn:
    role: Literal["user", "assistant"]
    parts: list[dict]


def iter_turns(conversation: list[dict]) -> Iterator[Turn]:
    for msg in conversation:
        role = msg.get("role")
        if role == "user" or role == "assistant":
            yield Turn(role=role, parts=msg.get("parts", []))


def render_transcript(
    turns: Iterable[Turn],
    tool_renderer: Callable[[dict], str | None],
) -> str:
    lines: list[str] = []
    for turn in turns:
        if turn.role == "user":
            for part in turn.parts:
                if part.get("type") == "text":
                    lines.append(f"{USER_PREFIX} {part['text']}")
        else:
            for part in turn.parts:
                ptype = part.get("type")
                if ptype == "text":
                    lines.append(f"{ASSISTANT_PREFIX} {part['text']}")
                elif ptype == "tool":
                    rendered = tool_renderer(part)
                    if rendered is not None:
                        lines.append(rendered)
    return "\n\n".join(lines)
