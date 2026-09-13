#!/usr/bin/env python3
"""
validation.py -- Pydantic validation layer for LLM JSON output.

llm.parse_json_response() guarantees *syntactically valid* JSON but says
nothing about its shape. Every caller then does unvalidated dict access, so a
judge that omits "overall_score" or returns it as a string silently poisons
the pipeline (score -1.0, KeyError three phases later).

This module pairs the healing parser with schema validation:

    raw  -> llm.parse_json_response()   (syntax + repair)
         -> <Model>.model_validate()      (shape + types)
         -> typed model instance

Validation failures raise OutputValidationError carrying an LLM-readable
feedback string, so callers can feed it back into a self-correction retry
(the same loop that already exists for JSON syntax errors).
"""

from core import llm
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from dotenv import load_dotenv


load_dotenv()


class OutputValidationError(ValueError):
    """LLM output was valid JSON but failed schema validation.

    .feedback is a concise, LLM-readable explanation suitable for inclusion
    in a self-correction retry prompt.
    """

    def __init__(self, feedback: str):
        super().__init__(feedback)
        self.feedback = feedback


class ScoreOutput(BaseModel):
    """Judge output for foundation/chapter evaluation (dynamic dimension keys)."""

    model_config = ConfigDict(extra="allow")

    overall_score: float = Field(ge=0, le=10)

    @field_validator("overall_score", mode="before")
    @classmethod
    def _coerce_score(cls, v):
        if isinstance(v, str):
            # removesuffix, not rstrip: rstrip("/10") strips a character SET,
            # corrupting "6.1" -> "6." and crashing on "10".
            return float(v.strip().removesuffix("/10").strip())
        return v


class NovelScoreOutput(BaseModel):
    """Judge output for full-novel evaluation."""

    model_config = ConfigDict(extra="allow")

    novel_score: float = Field(ge=0, le=10)

    @field_validator("novel_score", mode="before")
    @classmethod
    def _coerce_score(cls, v):
        if isinstance(v, str):
            # removesuffix, not rstrip: rstrip("/10") strips a character SET,
            # corrupting "6.1" -> "6." and crashing on "10".
            return float(v.strip().removesuffix("/10").strip())
        return v


class CompareOutput(BaseModel):
    """Head-to-head chapter comparison verdict."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    winner: str
    winner_chapter: int | None = None
    margin: str = ""
    decisive_moment: str = ""

    @field_validator("winner", mode="before")
    @classmethod
    def _normalize_winner(cls, v):
        s = str(v).strip().upper()
        if s in ("A", "B"):
            return s
        # Some judges echo the chapter number instead of the letter.
        if s.isdigit():
            return s
        raise ValueError(f"winner must be 'A' or 'B' (or a chapter number), got {v!r}")


def _format_validation_error(exc: ValidationError, context: str) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"- '{loc}': {err['msg']} (got {err['input']!r})")
    prefix = f"{context}: " if context else ""
    return (
        f"{prefix}JSON was valid but did not match the required schema:\n"
        + "\n".join(lines)
    )


def parse_validated(model_cls: type[BaseModel], text: str, context: str = "") -> BaseModel:
    """Parse LLM response text into a validated model instance.

    Raises OutputValidationError (subclass of ValueError) when the text
    contains no JSON or fails schema validation. On success returns an
    instance of model_cls; use .model_dump() where legacy dict access is
    still expected.
    """
    data = llm.parse_json_response(text)
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise OutputValidationError(
            _format_validation_error(exc, context)
        ) from exc


class MicroPlantCandidate(BaseModel):
    """One concrete prose detail that could pay off in a later chapter."""

    text: str = Field(min_length=1, max_length=240)
    kind: str = Field(default="object", max_length=24)


class MicroPlantExtract(BaseModel):
    """Judge output for post-keep micro-plant extraction."""

    new_plants: list[MicroPlantCandidate] = Field(default_factory=list)
    harvested_ids: list[str] = Field(default_factory=list)

    @field_validator("new_plants", mode="before")
    @classmethod
    def _cap_plants(cls, v):
        if isinstance(v, list):
            return v[:4]
        return v


def parse_validated_json_file(path, model_cls: type[BaseModel], context: str = "") -> BaseModel:
    """Load and validate a JSON file (e.g. eval logs written by earlier phases)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise OutputValidationError(
            _format_validation_error(exc, f"{context or path}")
        ) from exc
