"""Edit ops: every proposed edit is reversible data. Previews render at proxy res;
only approved ops are rendered to the master."""
from __future__ import annotations
from enum import Enum
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field
import uuid


def _id() -> str:
    return uuid.uuid4().hex[:10]


class NoteSource(str, Enum):
    typed = "typed"
    voice = "voice"


class Note(BaseModel):
    id: str = Field(default_factory=_id)
    t: Optional[float] = None          # seconds; None until the planner grounds it
    text: str
    source: NoteSource = NoteSource.typed


class Target(BaseModel):
    concept: str                      # SAM noun phrase, e.g. "phone", "man"
    object_id: Optional[int] = None   # stable SAM id, picked by set-of-mark when ambiguous


class Blur(BaseModel):
    kind: Literal["blur"] = "blur"
    target: Target
    strength: float = 0.8             # 0..1
    pixelate: bool = False


class TitleBehind(BaseModel):
    kind: Literal["title_behind"] = "title_behind"
    target: Target                    # the subject that stays in front
    text: str
    style: Literal["bold", "serif", "outline"] = "bold"


class Spotlight(BaseModel):
    kind: Literal["spotlight"] = "spotlight"
    target: Target                    # stays in colour; the rest is desaturated / dimmed
    amount: float = 0.7


class Ask(BaseModel):
    kind: Literal["ask"] = "ask"
    question: str
    options: list[str] = []


class Remove(BaseModel):
    kind: Literal["remove"] = "remove"
    target: Target                    # removed from every frame; the background is rebuilt (video inpainting)
    also: Optional[str] = None        # e.g. "his shadow": interaction regions removed with it
    engine: Optional[str] = None


class SkyReplace(BaseModel):
    kind: Literal["sky"] = "sky"
    target: Target = Target(concept="sky")
    look: str = "golden hour"


class Recolour(BaseModel):
    kind: Literal["recolour"] = "recolour"
    target: Target
    colour: str                       # spoken colour, e.g. "royal blue"
    hex: Optional[str] = None


OpBody = Union[Blur, TitleBehind, Spotlight, Ask, Remove, SkyReplace, Recolour]


class OpStatus(str, Enum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"


class Op(BaseModel):
    id: str = Field(default_factory=_id)
    note_id: str
    t0: float
    t1: float
    body: OpBody = Field(discriminator="kind")
    status: OpStatus = OpStatus.proposed
    rationale: str = ""
