/**
 * What the request spent its time on, step by step, as it happens.
 *
 * A streaming answer hides its own latency: a spinner, then text, with no
 * account of the seconds in between. Each pipeline step reports itself as it
 * starts and again as it ends, and this renders that account.
 *
 * Nothing here knows which stages exist. The labels are written by the stages
 * themselves and arrive on the wire, so a step added to the pipeline appears
 * here on its own — which is the whole reason the server sends wording rather
 * than a code to look up.
 */

import type { StageEventData } from '../../api/types'
import { Spinner } from '../../components/Spinner'

interface StageTimelineProps {
  stages: StageEventData[]
}

/** Seconds, at the precision a person reads latency in. */
function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(2)}s`
}

/** The marker for a step: running, finished, or failed. */
function Marker({ status }: { status: StageEventData['status'] }) {
  if (status === 'started') return <Spinner className="size-3" />

  return (
    <span
      aria-hidden
      className={`inline-block size-3 rounded-full ${
        status === 'failed' ? 'bg-state-orphaned' : 'bg-state-current'
      }`}
    />
  )
}

export function StageTimeline({ stages }: StageTimelineProps) {
  if (stages.length === 0) return null

  // Only finished steps have a duration, so a running request reports the time
  // it has accounted for rather than a total that keeps moving.
  const settled = stages.filter((stage) => stage.status !== 'started')
  const total = settled.reduce((sum, stage) => sum + stage.elapsed_ms, 0)

  return (
    <section className="mb-5 rounded-lg border border-slate-200 bg-white px-4 py-3">
      <header className="mb-2 flex items-baseline justify-between gap-3">
        <h2 className="text-xs font-medium tracking-wide text-slate-400 uppercase">
          Pipeline
        </h2>
        {settled.length > 0 ? (
          <span className="tabular text-xs text-slate-400">{seconds(total)} accounted for</span>
        ) : null}
      </header>

      <ol className="space-y-1.5">
        {stages.map((stage) => (
          <li key={stage.sequence} className="flex items-baseline gap-2.5 text-xs">
            <span className="flex w-3 shrink-0 justify-center self-center">
              <Marker status={stage.status} />
            </span>

            <span
              className={
                stage.status === 'failed'
                  ? 'shrink-0 font-medium text-state-orphaned'
                  : 'shrink-0 font-medium text-slate-700'
              }
            >
              {stage.label}
            </span>

            {/* The stage's own account of what it did — kept subordinate to the
                label, and allowed to be missing. */}
            {stage.detail ? (
              <span
                className={`min-w-0 flex-1 truncate ${
                  stage.status === 'failed' ? 'text-state-orphaned' : 'text-slate-400'
                }`}
                title={stage.detail}
              >
                {stage.detail}
              </span>
            ) : (
              <span className="min-w-0 flex-1" />
            )}

            <span className="tabular shrink-0 text-slate-500">
              {stage.status === 'started' ? '…' : seconds(stage.elapsed_ms)}
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}
