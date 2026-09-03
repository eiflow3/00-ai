"""POST /chat endpoint — runs retrieval, then streams the LLM answer via SSE.

The router stays thin on purpose: it validates input, delegates each pipeline
stage to a service, and serialises the results as SSE events. Retrieval,
prompt construction, provider calls, and pricing all live in app.services.
"""

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.docs.chat import CHAT_DESCRIPTION, CHAT_MODELS_DESCRIPTION, CHAT_RESPONSES
from app.schemas.chat import (
    BlockedEventData,
    ChatRequest,
    ModelOption,
    ChatStreamBlockedEvent,
    ChatStreamErrorEvent,
    ChatStreamGovernanceEvent,
    ChatStreamRetrievalEvent,
    ChatStreamStageEvent,
    ChatStreamTraceEvent,
    ChatStreamUsageEvent,
    ErrorEventData,
    GovernanceEventData,
    RetrievalEventData,
    TraceEventData,
)
from app.schemas.governance import (
    GovernanceMode,
    GovernancePolicy,
    GovernanceResult,
)
from app.schemas.retrieval import RetrievalResult
from app.services import chat_trace, chunk_variants, prompt_store
from app.services.chunk_variants import UnknownVariant
from app.services.cost_tracker import calculate_cost
from app.services.governance import audit as governance_audit
from app.services.governance import policy as governance_policy
from app.services.governance import runner as governance_runner
from app.services.governance.runner import GovernanceStageFailure
from app.services.llm import get_adapter
from app.services.llm.catalog import DEFAULT_MODELS, list_models
from app.services.pipeline_timeline import Timeline
from app.services.prompt_builder import build_messages, resolve_system_prompt
from app.services.retrieval import retrieve

router = APIRouter()

# Fallback when a provider has no default in the catalog.
_FALLBACK_MODEL = "gpt-5.6-terra"


def _sse(
    event: ChatStreamTraceEvent
    | ChatStreamRetrievalEvent
    | ChatStreamStageEvent
    | ChatStreamErrorEvent
    | ChatStreamGovernanceEvent
    | ChatStreamBlockedEvent
    | ChatStreamUsageEvent,
) -> dict:
    """Serialise a typed stream event into the dict sse-starlette expects.

    Going through the schema models keeps every event's payload defined in
    one place — the same models the OpenAPI docs describe.
    """
    payload = event.data
    # Retrieval and error payloads are pydantic models; the usage payload is
    # the cost tracker's dataclass, which rounds its own floats in to_dict().
    data = (
        payload.model_dump_json()
        if isinstance(payload, BaseModel)
        else json.dumps(payload.to_dict())
    )
    return {"event": event.event, "data": data}


def _governance_note(result: GovernanceResult) -> str:
    """The one-line detail a governance stage shows on the timeline."""
    if not result.screened:
        return "skipped — governance is off"
    if not result.findings:
        return "no findings"
    return f"{len(result.findings)} finding(s) · {result.verdict}"


@router.get(
    "/chat/models",
    response_model=list[ModelOption],
    summary="List the providers and models this deployment offers",
    response_description="Every provider/model pair, and whether it is usable here.",
    description=CHAT_MODELS_DESCRIPTION,
)
def chat_models() -> list[ModelOption]:
    """List the provider/model pairs a client may choose between.

    Returns:
        One entry per pair, carrying display labels, whether this deployment
        has the credentials for it, and any caveat worth showing.
    """
    return [ModelOption(**entry) for entry in list_models()]


@router.post(
    "/chat",
    summary="Stream a retrieval-grounded chat response",
    response_description="SSE stream of retrieval results followed by the generated answer.",
    description=CHAT_DESCRIPTION,
    responses=CHAT_RESPONSES,
)
async def chat(body: ChatRequest):
    """Retrieve relevant context, then stream a grounded chat response.

    1. Resolves the correct adapter via the factory.
    2. Runs the retrieval pipeline (embed query -> vector search -> rank).
    3. Emits a "retrieval" event with each chunk and its similarity score.
    4. Streams the LLM's text deltas, grounded in those chunks.
    5. Sends a final "usage" event with token counts and cost breakdown.

    Throughout, each step reports itself as a "stage" event — as it starts and
    again as it ends, carrying how long it took. A step added to the pipeline
    later shows up on the client without anything here listing it.
    """
    # Get the adapter for the requested provider.
    try:
        adapter = get_adapter(body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # And settle which chunking answers, before the stream opens. Once it has,
    # the status code is already sent and a bad variant could only be reported
    # as an event — which retrieval would treat as best-effort and answer
    # ungrounded, so a typo would read as a model that had lost its context.
    try:
        chunk_variants.space_for(body.chunk_variant)
    except UnknownVariant as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Resolve the model — use the client's override or fall back to the default.
    model = body.model or DEFAULT_MODELS.get(body.provider, _FALLBACK_MODEL)

    # Read the prompts before the stream opens. Once the response has begun a
    # store failure could only be reported as an event, and a request whose
    # wording could not be resolved has nothing worth streaming anyway.
    prompts = await prompt_store.active()

    # What this request will actually run under, which is what the trace has to
    # record: the client's own system prompt if it sent one, else the configured
    # default. "What was sent" and "what the client asked for" are no longer the
    # same thing, and only the first can be judged later.
    system_prompt = resolve_system_prompt(body.system_prompt, prompts)

    # The governance policy this request runs under: the configured default,
    # with the one field a request may override (its mode) layered on top.
    # Resolved before the stream opens, like everything else that can refuse.
    policy = governance_policy.resolve(
        default=governance_policy.default_policy(),
        request=(
            GovernancePolicy(mode=body.governance_mode)
            if body.governance_mode is not None
            else None
        ),
    )

    async def event_generator() -> AsyncIterator[dict]:
        """Yield the trace id, each stage as it runs, the deltas, then the usage."""
        # Recording starts before anything can fail, so a request that dies
        # halfway still leaves behind what it managed to do.
        recorder = chat_trace.start(body, model, system_prompt)

        # Every stage of the pipeline reports itself here, and each event is put
        # on the wire as soon as it is recorded — a stage the client only hears
        # about once the answer arrives has stopped being progress.
        timeline = Timeline()

        # First event on the wire: the client needs this id before the answer
        # arrives, because every later judgement is filed against it.
        yield _sse(ChatStreamTraceEvent(data=TraceEventData(trace_id=recorder.trace_id)))

        try:
            # --- Governance: the question -------------------------------------
            # Screened before retrieval, so nothing the policy would redact is
            # embedded, searched for, or sent to a provider. Everything after
            # this point uses `query`, never `body.query`.
            query = body.query
            try:
                async with timeline.stage(
                    "governance_inbound", "Screening the question"
                ) as stage:
                    inbound = await governance_runner.run(body.query, policy)
                    stage.note(_governance_note(inbound))
            except GovernanceStageFailure as exc:
                # Fail closed: enforce could not screen, so nothing may proceed.
                for event in timeline.drain():
                    yield _sse(ChatStreamStageEvent(data=event))
                recorder.record_error("governance_inbound", str(exc))
                yield _sse(ChatStreamErrorEvent(
                    data=ErrorEventData(stage="governance_inbound", message=str(exc))
                ))
                return
            for event in timeline.drain():
                yield _sse(ChatStreamStageEvent(data=event))

            inbound_findings = governance_audit.summarize(inbound.findings, policy)
            yield _sse(ChatStreamGovernanceEvent(data=GovernanceEventData(
                point="inbound",
                mode=policy.mode,
                screened=inbound.screened,
                verdict=inbound.verdict,
                findings=inbound_findings,
            )))

            if inbound.verdict == "blocked":
                # Not an error — policy examined the question and refused it.
                recorder.record_error(
                    "governance_inbound", "Blocked by governance policy."
                )
                yield _sse(ChatStreamBlockedEvent(data=BlockedEventData(
                    point="inbound",
                    message=(
                        "The question contains content this deployment's "
                        "governance policy refuses to process."
                    ),
                    findings=inbound_findings,
                )))
                return

            query = inbound.output_text

            # --- Retrieval phase --------------------------------------------
            # Client-supplied chunks win, so a caller that already ran retrieval
            # (or is replaying a conversation) isn't charged for it twice.
            if body.context_chunks:
                async with timeline.stage("context", "Using the supplied context") as stage:
                    result = RetrievalResult(
                        query=query,
                        chunks=body.context_chunks,
                        total_searched=len(body.context_chunks),
                    )
                    stage.note(f"{len(body.context_chunks)} chunk(s) sent with the request")
                for event in timeline.drain():
                    yield _sse(ChatStreamStageEvent(data=event))
            elif body.use_rag:
                # Run as a task so its stages reach the client while it is still
                # working, rather than all at once when it returns.
                search = asyncio.ensure_future(retrieve(
                    query,
                    top_k=body.top_k,
                    score_threshold=body.score_threshold,
                    embedding_model=body.embedding_model,
                    timeline=timeline,
                    variant=body.chunk_variant,
                ))
                async for event in timeline.follow(search):
                    yield _sse(ChatStreamStageEvent(data=event))

                try:
                    # The task has already finished; this only unwraps it.
                    result = await search
                except Exception as exc:
                    # Retrieval is best-effort: tell the client it failed, then
                    # answer without context rather than dropping the request.
                    # Which step failed already went out as a stage event.
                    recorder.record_error("retrieval", str(exc))
                    yield _sse(ChatStreamErrorEvent(
                        data=ErrorEventData(stage="retrieval", message=str(exc))
                    ))
                    result = RetrievalResult(query=query)
            else:
                result = RetrievalResult(query=query)

            # Captured before the answer exists: this is the evidence a later
            # evaluation is judged against, and the index may change meanwhile.
            recorder.record_retrieval(result)

            # Emit the retrieved chunks — with scores — before any generated text,
            # so the client can render citations while the answer streams in.
            yield _sse(ChatStreamRetrievalEvent(data=RetrievalEventData(
                query=result.query,
                chunks=result.chunks,
                total_searched=result.total_searched,
                embedding_model=body.embedding_model,
            )))

            # --- Generation phase --------------------------------------------
            async with timeline.stage("prompt", "Building the prompt") as stage:
                messages = build_messages(
                    query=query,
                    chunks=result.chunks,
                    system_prompt=system_prompt,
                    # False only when the caller asked for an ungrounded answer:
                    # nothing was searched, so the empty-retrieval fallback would
                    # be reporting a failure that never happened.
                    grounded=body.use_rag or bool(body.context_chunks),
                    prompts=prompts,
                )
                stage.note(f"{len(result.chunks)} chunk(s) in context")
            for event in timeline.drain():
                yield _sse(ChatStreamStageEvent(data=event))

            # Stream text deltas from the LLM adapter. The response status was
            # sent when the stream opened, so a provider failure here cannot become
            # an HTTP error — it has to be an event, or the connection just dies
            # mid-answer with nothing explaining why.
            generation_error = ""
            # Under enforce the answer is held back and screened whole before
            # anything reaches the client — a masked token stream would leak
            # the value across delta boundaries. Under audit_only and off the
            # deltas stream live, exactly as before.
            hold_answer = policy.mode is GovernanceMode.ENFORCE
            answer_parts: list[str] = []
            async with timeline.stage("generation", "Generating the answer") as stage:
                # The stage has to be announced before the first token, or the
                # slowest step of the request is the one with nothing on screen.
                for event in timeline.drain():
                    yield _sse(ChatStreamStageEvent(data=event))

                deltas = 0
                first_token_ms = 0
                try:
                    async for token in adapter.stream(messages, model, body.temperature):
                        # Time to first token is the wait a person actually feels,
                        # and it is not visible in the stage's total.
                        if not deltas:
                            first_token_ms = stage.elapsed_ms
                        deltas += 1
                        recorder.append_answer(token)
                        answer_parts.append(token)
                        if hold_answer:
                            continue
                        # Each yield sends a "data: <token>\n\n" SSE event to the client.
                        yield {"data": token}
                except Exception as exc:
                    # Failed here rather than raised: the stage has to be closed
                    # as failed, but this generator owns how the client is told.
                    stage.fail(str(exc))
                    generation_error = str(exc)
                else:
                    stage.note(
                        f"first token in {first_token_ms / 1000:.2f}s · {deltas} deltas"
                        if deltas
                        else "the model returned no text"
                    )

            for event in timeline.drain():
                yield _sse(ChatStreamStageEvent(data=event))

            if generation_error:
                recorder.record_error("generation", generation_error)
                yield _sse(ChatStreamErrorEvent(
                    data=ErrorEventData(stage="generation", message=generation_error)
                ))
                return

            # --- Governance: the answer ---------------------------------------
            answer_text = "".join(answer_parts)
            try:
                async with timeline.stage(
                    "governance_outbound", "Screening the answer"
                ) as stage:
                    outbound = await governance_runner.run(answer_text, policy)
                    stage.note(_governance_note(outbound))
            except GovernanceStageFailure as exc:
                # Fail closed: the held-back answer must not go out unscreened.
                for event in timeline.drain():
                    yield _sse(ChatStreamStageEvent(data=event))
                recorder.record_error("governance_outbound", str(exc))
                yield _sse(ChatStreamErrorEvent(
                    data=ErrorEventData(stage="governance_outbound", message=str(exc))
                ))
                return
            for event in timeline.drain():
                yield _sse(ChatStreamStageEvent(data=event))

            outbound_findings = governance_audit.summarize(outbound.findings, policy)
            yield _sse(ChatStreamGovernanceEvent(data=GovernanceEventData(
                point="outbound",
                mode=policy.mode,
                screened=outbound.screened,
                verdict=outbound.verdict,
                findings=outbound_findings,
            )))

            if outbound.verdict == "blocked":
                recorder.record_error(
                    "governance_outbound", "Blocked by governance policy."
                )
                yield _sse(ChatStreamBlockedEvent(data=BlockedEventData(
                    point="outbound",
                    message=(
                        "The generated answer was withheld by this "
                        "deployment's governance policy."
                    ),
                    findings=outbound_findings,
                )))
                return

            if hold_answer:
                # The whole answer, screened, in one delta — the price of
                # enforce is latency, never an unscreened token.
                yield {"data": outbound.output_text}

            # After streaming completes, calculate and send cost breakdown.
            # The adapter populates self.usage with token counts after the stream.
            if adapter.usage:
                cost = calculate_cost(model, adapter.usage)
                recorder.record_usage(cost)
                # Send a separate "usage" event so the client can distinguish
                # cost metadata from the text stream.
                yield _sse(ChatStreamUsageEvent(data=cost))
        except (asyncio.CancelledError, GeneratorExit):
            # The client hung up mid-answer. Recorded as such, so a half-written
            # answer is never read later as one the model chose to end there.
            recorder.record_cancelled()
            raise
        finally:
            # Detached rather than awaited: this block may be running *because*
            # the connection died, which is exactly when awaiting is unreliable
            # — and a disconnect is no reason to lose the record.
            recorder.persist()

    # Return the SSE streaming response.
    # media_type ensures the browser/client treats this as an event stream.
    return EventSourceResponse(event_generator())
