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

from pydantic import (
    BaseModel, ConfigDict, Field, RootModel, ValidationError,
    field_validator, model_validator,
)
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


class TonalDriftVerdict(BaseModel):
    """Judge verdict for the outline tonal-drift gate.

    `has_drift` is required: the gate must not default to "no drift" when
    the judge omits the field — that silently disables the gatekeeper.
    """

    model_config = ConfigDict(extra="allow")

    has_drift: bool
    analysis: str = ""
    violations: list[str] = Field(default_factory=list)

    @field_validator("has_drift", mode="before")
    @classmethod
    def _coerce_bool(cls, v):
        if isinstance(v, str):
            lowered = v.strip().lower()
            if lowered in ("true", "yes", "1"):
                return True
            if lowered in ("false", "no", "0", ""):
                return False
        return v

    @field_validator("violations", mode="before")
    @classmethod
    def _coerce_violations(cls, v):
        if isinstance(v, str):
            return [v] if v.strip() else []
        return v


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


# ---------------------------------------------------------------------------
# Foundation: title tournament
# ---------------------------------------------------------------------------


class TitleJudge(BaseModel):
    """One title-tournament judge persona."""

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    persona: str = Field(min_length=1)


class TitleJudgePanel(RootModel[list[TitleJudge]]):
    """The 4-judge panel. Fewer than 4 judges fails validation."""

    @model_validator(mode="after")
    def _require_four(self):
        if len(self.root) < 4:
            raise ValueError(f"expected >= 4 judges, got {len(self.root)}")
        self.root = self.root[:4]
        return self


class TitleScoreMap(RootModel[dict[str, int]]):
    """Judge output: title string -> integer score."""

    @model_validator(mode="before")
    @classmethod
    def _coerce_scores(cls, v):
        if not isinstance(v, dict):
            raise ValueError(f"expected a JSON object of title->score, got {type(v).__name__}")
        out = {}
        for k, val in v.items():
            try:
                out[str(k)] = int(val)
            except (TypeError, ValueError) as e:
                raise ValueError(f"score for {k!r} is not an integer: {val!r}") from e
        return out


# ---------------------------------------------------------------------------
# Stage scripts: chapter outline, cuts, panel, titles, slop repair
# ---------------------------------------------------------------------------


class ChapterOutlineEntry(BaseModel):
    """`build_outline.process_chapter_outline` reconstruction of one chapter."""

    model_config = ConfigDict(extra="allow")

    title: str = ""
    location: str = ""
    characters: list[str] = Field(default_factory=list)
    summary: str = ""
    orientation_facts: list[str] = Field(default_factory=list)
    scene_stakes: str = ""
    beats: list[str] = Field(default_factory=list)
    try_fail: str = ""
    plants: list[str] = Field(default_factory=list)
    harvests: list[str] = Field(default_factory=list)
    emotional_arc: str = ""
    chapter_question: str = ""


class HarvestAttribution(BaseModel):
    """One payoff matched to the chapter whose plant it resolves."""

    model_config = ConfigDict(extra="allow")

    index: int
    planted_chapter: int | None = None


class HarvestAttributions(BaseModel):
    """Output of the attribution pass: which earlier plant each payoff resolves."""

    model_config = ConfigDict(extra="allow")

    attributions: list[HarvestAttribution] = Field(default_factory=list)


class CutEntry(BaseModel):
    """One adversarial-edit cut recommendation."""

    model_config = ConfigDict(extra="allow")

    quote: str = ""
    type: str = ""


class AdversarialCuts(BaseModel):
    """`adversarial_edit.edit_chapter` verdict."""

    model_config = ConfigDict(extra="allow")

    cuts: list[CutEntry] = Field(default_factory=list)
    total_cuttable_words: int = 0
    overall_fat_percentage: float = 0.0
    one_sentence_verdict: str = ""
    tightest_passage: str = ""
    loosest_passage: str = ""


class ReaderPanelAnswers(BaseModel):
    """One reader persona's answers about the novel as a whole."""

    model_config = ConfigDict(extra="allow")

    momentum_loss: str = ""
    earned_ending: str = ""
    cut_candidate: str = ""
    missing_scene: str = ""
    thinnest_character: str = ""
    best_scene: str = ""
    worst_scene: str = ""
    would_recommend: str = ""
    haunts_you: str = ""
    next_book: str = ""

    @model_validator(mode="before")
    @classmethod
    def _stringify(cls, v):
        if isinstance(v, dict):
            return {k: ("" if val is None else str(val)) for k, val in v.items()}
        return v


class SanitizedTitles(RootModel[dict[int, str]]):
    """Chapter number -> rewritten title."""

    @model_validator(mode="before")
    @classmethod
    def _coerce_keys(cls, v):
        if not isinstance(v, dict):
            raise ValueError(f"expected a JSON object of chapter->title, got {type(v).__name__}")
        out = {}
        for k, val in v.items():
            try:
                out[int(k)] = str(val).strip()
            except (TypeError, ValueError) as e:
                raise ValueError(f"chapter key {k!r} is not a number") from e
        return out


class SlopRepairPatch(RootModel[dict[str, str]]):
    """Paragraph id (`p1`, `p2`, …) -> rewritten paragraph."""

    @model_validator(mode="before")
    @classmethod
    def _require_strings(cls, v):
        if not isinstance(v, dict):
            raise ValueError(f"expected a JSON object of id->text, got {type(v).__name__}")
        for k, val in v.items():
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"paragraph {k!r} has no usable replacement text")
        return v
