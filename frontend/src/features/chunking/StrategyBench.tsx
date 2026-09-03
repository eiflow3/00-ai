/**
 * The bench: pick a file, pick a way of cutting it, and see what that costs.
 *
 * Preview and Index are deliberately separate buttons rather than one flow.
 * Previewing is free and instant; indexing spends money and takes a minute, and
 * a screen that blurred the two would have people paying to find out something
 * they could have seen for nothing.
 *
 * "Index all four" is the primary action because comparing is the whole point.
 * One click queues the same file under every strategy, and the run embeds each
 * of them on its own terms.
 *
 * The file picker also offers the whole bucket. Indexing used to start from the
 * Sources screen, which is where a "everything that needs it" sweep belonged;
 * moving indexing here would have quietly cost that, so it moved too. Preview
 * still needs one file, because a preview is a document being cut.
 */

import type {
  ChunkStrategySpec,
  ChunkStrategy,
  GovernanceMode,
  SourceStatus,
} from '../../api/types'
import { GovernancePicker } from '../../components/GovernancePicker'
import { Spinner } from '../../components/Spinner'

/**
 * The picker value standing for "every readable file in the bucket".
 *
 * A sentinel rather than an empty selection, because "all of them" and "none
 * chosen yet" lead to opposite buttons being enabled.
 */
export const ALL_FILES = '*'

interface StrategyBenchProps {
  sources: SourceStatus[]
  strategies: ChunkStrategySpec[]
  sourceKey: string
  strategy: ChunkStrategy | null
  chunkSize: number
  chunkOverlap: number
  /** Governance mode this run will index under. '' means the server default. */
  governanceMode: GovernanceMode | ''
  previewing: boolean
  indexing: boolean
  onSourceKey: (sourceKey: string) => void
  onStrategy: (strategy: ChunkStrategy) => void
  onChunkSize: (size: number) => void
  onChunkOverlap: (overlap: number) => void
  onGovernanceMode: (mode: GovernanceMode | '') => void
  onPreview: () => void
  onIndex: () => void
  onIndexAll: () => void
}

export function StrategyBench({
  sources,
  strategies,
  sourceKey,
  strategy,
  chunkSize,
  chunkOverlap,
  governanceMode,
  previewing,
  indexing,
  onSourceKey,
  onStrategy,
  onChunkSize,
  onChunkOverlap,
  onGovernanceMode,
  onPreview,
  onIndex,
  onIndexAll,
}: StrategyBenchProps) {
  // `orphaned` means the vectors outlived the file, so there is nothing left to
  // read; `unsupported` means we cannot decode it. Neither can be cut.
  const readable = sources.filter(
    (source) => source.state !== 'unsupported' && source.state !== 'orphaned',
  )

  const chosen = strategies.find((spec) => spec.id === strategy) ?? null

  // The overlap has to leave room to advance, or the splitter could not move.
  const geometryValid = chunkOverlap < chunkSize
  const everything = sourceKey === ALL_FILES
  const chosenSomething = sourceKey !== ''
  // A preview cuts one document and shows the chunks, so the whole bucket is
  // not a thing it can answer for.
  const canPreview = chosenSomething && !everything && strategy !== null && geometryValid
  const canIndex = chosenSomething && strategy !== null && geometryValid

  return (
    <section className="mb-6 rounded-lg border border-slate-200 bg-white px-4 py-4">
      <label className="block">
        <span className="mb-1 block text-xs font-medium text-slate-600">Source file</span>
        <select
          value={sourceKey}
          onChange={(event) => onSourceKey(event.target.value)}
          className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
        >
          <option value="">Choose a file…</option>
          <option value={ALL_FILES}>
            Every readable file ({readable.length})
          </option>
          {readable.map((source) => (
            <option key={source.source_key} value={source.source_key}>
              {source.source_key}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="mt-4">
        <legend className="mb-1.5 text-xs font-medium text-slate-600">Strategy</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {strategies.map((spec) => (
            <button
              key={spec.id}
              type="button"
              onClick={() => onStrategy(spec.id)}
              aria-pressed={strategy === spec.id}
              className={`rounded-md border px-3 py-2 text-left ${
                strategy === spec.id
                  ? 'border-slate-900 bg-slate-50'
                  : 'border-slate-200 hover:border-slate-300'
              }`}
            >
              <span className="block text-sm font-medium text-slate-900">{spec.label}</span>
              <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">
                {spec.summary}
              </span>
            </button>
          ))}
        </div>
      </fieldset>

      {chosen ? (
        <p className="mt-2 text-xs leading-relaxed text-slate-400">{chosen.detail}</p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label>
          <span className="mb-1 block text-xs font-medium text-slate-600">Chunk size</span>
          <input
            type="number"
            min={64}
            max={8000}
            step={64}
            value={chunkSize}
            onChange={(event) => onChunkSize(Number(event.target.value))}
            className="tabular w-28 rounded border border-slate-300 px-2 py-1.5 text-sm"
          />
        </label>

        <label>
          <span className="mb-1 block text-xs font-medium text-slate-600">Overlap</span>
          <input
            type="number"
            min={0}
            max={4000}
            step={16}
            value={chunkOverlap}
            onChange={(event) => onChunkOverlap(Number(event.target.value))}
            disabled={chosen !== null && !chosen.honours_overlap}
            className="tabular w-28 rounded border border-slate-300 px-2 py-1.5 text-sm disabled:bg-slate-50 disabled:text-slate-400"
          />
        </label>

        <span className="text-xs text-slate-400">tokens</span>

        <GovernancePicker
          value={governanceMode}
          onChange={onGovernanceMode}
          disabled={indexing}
        />
      </div>

      {!geometryValid ? (
        <p className="mt-2 text-xs text-state-stale">
          The overlap has to be smaller than the chunk size, or the splitter
          cannot move forward.
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
        <p className="text-xs text-slate-500">
          {strategy !== null && geometryValid ? (
            <>
              Creates{' '}
              <span className="font-mono text-slate-700">
                {strategy}-{chunkSize}-{chunkOverlap}
              </span>
              {everything
                ? ', holding every readable file in the bucket.'
                : ', kept apart from the space your answers come from.'}
            </>
          ) : (
            'Pick a file and a strategy.'
          )}
        </p>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onPreview}
            disabled={!canPreview || previewing}
            title={
              everything ? 'A preview cuts one document. Pick a single file.' : undefined
            }
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-40"
          >
            {previewing ? <Spinner /> : null} Preview — free
          </button>
          <button
            type="button"
            onClick={onIndex}
            disabled={!canIndex || indexing}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-40"
          >
            Index this one
          </button>
          <button
            type="button"
            onClick={onIndexAll}
            disabled={!chosenSomething || !geometryValid || indexing}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-30"
          >
            Index all {strategies.length}
          </button>
        </div>
      </div>
    </section>
  )
}
