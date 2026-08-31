"""Prompt schemas — the templates the Generation phase is assembled from.

These used to be constants in `app.services.prompt_builder`, which meant the
wording that decides how an answer is grounded could only be changed by editing
Python and restarting.  Every one of them is now a record: a default that ships
with the code, an optional override someone saved, and the variables the text
is allowed to interpolate.

The variables are part of the contract, not documentation.  A template naming
something the pipeline does not supply is rejected when it is saved, rather than
failing halfway through a stream where the only symptom is a missing answer.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PromptGroup(str, Enum):
    """Which part of the system a prompt steers.

    The tab shows every prompt the app is assembled from, and those are no
    longer all one pipeline: answering a question and drafting an evaluation
    set are different jobs with different failure modes.  Grouping them keeps
    the list readable, and keeps someone tuning the generator from wondering
    why their edit did not change any answers.
    """

    # Steers how a question is answered.
    CHAT = "chat"

    # Steers how a golden set is drafted from a source document.
    GOLDEN = "golden"


class PromptId(str, Enum):
    """The prompts the pipeline assembles a request from.

    Closed on purpose.  A client picks from this set rather than inventing an
    id, for the same reason the evaluation tags are served rather than guessed:
    an override filed under a name nothing reads is worse than no override.
    """

    # Steers the model for the whole request.
    SYSTEM = "system"

    # Wraps the retrieved chunks into the message that carries them.
    CONTEXT_BLOCK = "context_block"

    # Renders one retrieved chunk inside that block.
    CHUNK_FORMAT = "chunk_format"

    # Used instead of the context block when retrieval came back empty.
    NO_CONTEXT = "no_context"

    # Drafts questions answerable from one section of a source document.
    GOLDEN_SECTION = "golden_section"

    # Drafts questions that must join two sections, or compute across them.
    GOLDEN_CROSS_SECTION = "golden_cross_section"

    # Drafts questions the document does not answer, where refusing is correct.
    GOLDEN_UNANSWERABLE = "golden_unanswerable"


class PromptVariable(BaseModel):
    """One value a template may interpolate, and whether it must."""

    # Name as written in the template, without braces.
    name: str = Field(..., description="Placeholder name, used as {name}")

    # What the pipeline substitutes for it.
    description: str = Field(..., description="What this value holds at render time")

    # Whether a template that omits it is rejected.
    required: bool = Field(
        default=False,
        description="True when a template that leaves this out would drop information",
    )

    # A realistic value, used for the preview and to test-render a saved edit.
    example: str = Field(default="", description="Stand-in value used to preview")


class Prompt(BaseModel):
    """One prompt as it currently stands, with the default it was built from."""

    id: PromptId = Field(..., description="Stable id to address this prompt by")

    # What this prompt steers, so the editor can section the list.
    group: PromptGroup = Field(
        default=PromptGroup.CHAT, description="Which part of the system this steers"
    )

    label: str = Field(..., description="Human-readable name")
    description: str = Field(..., description="What this text is for")

    # Written for a person deciding whether an edit here will affect them.
    applies_when: str = Field(
        ..., description="When the pipeline uses this template"
    )

    # The text in force — the override if one was saved, else the default.
    template: str = Field(..., description="The text the pipeline will use")

    # What shipped with the code, kept so a reset has something to return to.
    default_template: str = Field(..., description="The text this prompt ships with")

    variables: list[PromptVariable] = Field(
        default_factory=list, description="Values this template may interpolate"
    )

    # True when an override is in force, so a client can offer to reset it.
    edited: bool = Field(
        default=False, description="Whether a saved override is overriding the default"
    )

    updated_at: Optional[datetime] = Field(
        default=None, description="When the override was saved, if there is one"
    )

    # False when blanking the template turns the prompt off rather than
    # producing an empty message.
    optional: bool = Field(
        default=False,
        description="Whether an empty template is allowed, and means 'skip this'",
    )


class PromptUpdateRequest(BaseModel):
    """Body for saving an override."""

    template: str = Field(
        ...,
        max_length=20_000,
        description="Replacement text. Empty clears an optional prompt entirely.",
    )


class PromptMessage(BaseModel):
    """One assembled message, as the provider adapters receive it."""

    role: str = Field(..., description="Message role: 'system' or 'user'")
    content: str = Field(..., description="Fully interpolated message text")


class PromptPreviewRequest(BaseModel):
    """Body for rendering the templates into the messages they produce."""

    query: str = Field(
        default="What was revenue last year?",
        max_length=2_000,
        description="Question to stand in for the user's",
    )

    # How many stand-in chunks to render, so the repetition is visible.
    chunk_count: int = Field(
        default=2, ge=0, le=5, description="Sample chunks to render into the block"
    )

    # False previews the ungrounded path, where the context block never runs.
    grounded: bool = Field(
        default=True, description="Preview the retrieval-grounded path"
    )


class PromptPreview(BaseModel):
    """The message list the current templates would produce."""

    messages: list[PromptMessage] = Field(
        default_factory=list, description="Messages in the order they are sent"
    )

    # Cheap proxy for how much of the context window the scaffolding costs.
    character_count: int = Field(
        default=0, ge=0, description="Total characters across every message"
    )
