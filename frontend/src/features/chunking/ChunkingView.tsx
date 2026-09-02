/**
 * The chunking screen: cut one file several ways and find out which is better.
 *
 * The order down the page is the order the work happens in. Choose a strategy
 * and preview it, which costs nothing. Index the ones worth embedding, each
 * into its own space. See what exists. Then score every variant against a
 * golden set, which is the only step that actually answers the question.
 *
 * Nothing here can affect a production answer. Every variant's vectors live in
 * their own namespace in a separate index, so a query against one cannot return
 * another's chunks and the index the app normally answers from is never
 * written to.
 */

import { useCallback, useState } from 'react'

import { PreviewPanel } from './PreviewPanel'
import { Scoreboard } from './Scoreboard'
import { StrategyBench } from './StrategyBench'
import { VariantsTable } from './VariantsTable'
import type { ChunkStrategy, ChunkVariant } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { useChunkPreview } from '../../hooks/useChunkPreview'
import { useGoldenSets } from '../../hooks/useGoldenSets'
import { useIndexRun } from '../../hooks/useIndexRun'
import { useSources } from '../../hooks/useSources'
import { useVariantScore } from '../../hooks/useVariantScore'
import { useVariants } from '../../hooks/useVariants'
import { IndexProgress } from '../sources/IndexProgress'

/** Where the geometry starts. The same defaults the pipeline has always used. */
const DEFAULT_CHUNK_SIZE = 512
const DEFAULT_CHUNK_OVERLAP = 64

interface ChunkingViewProps {
  /** Opens the chat screen with this variant already selected. */
  onAsk: (variantId: string) => void
}

export function ChunkingView({ onAsk }: ChunkingViewProps) {
  // Both of these are "the person's pick, or the obvious default" — derived
  // during render rather than written by an effect, so the default falls into
  // place as soon as the lists arrive and a pick always wins over it.
  const [pickedSource, setPickedSource] = useState<string | null>(null)
  const [pickedStrategy, setPickedStrategy] = useState<ChunkStrategy | null>(null)
  const [chunkSize, setChunkSize] = useState(DEFAULT_CHUNK_SIZE)
  const [chunkOverlap, setChunkOverlap] = useState(DEFAULT_CHUNK_OVERLAP)
  const [deleting, setDeleting] = useState<ChunkVariant | null>(null)

  const sources = useSources()
  const preview = useChunkPreview()
  const variants = useVariants()
  const golden = useGoldenSets()
  const score = useVariantScore()

  // A finished run has written new vectors, and the variants list is read from
  // the index — so it is only right again once the run has ended.
  const onSettled = useCallback(() => variants.refresh(), [variants])
  const run = useIndexRun(onSettled)

  // One file in the bucket means one obvious choice; making somebody pick from
  // a list of one is a step that teaches them nothing.
  const sourceKey =
    pickedSource ??
    (sources.sources.length === 1 ? sources.sources[0].source_key : '')

  const strategy = pickedStrategy ?? preview.strategies[0]?.id ?? null

  const handlePreview = useCallback(() => {
    if (sourceKey === '' || strategy === null) return
    void preview.run(sourceKey, {
      strategy,
      chunk_size: chunkSize,
      chunk_overlap: chunkOverlap,
    })
  }, [chunkOverlap, chunkSize, preview, sourceKey, strategy])

  const handleIndex = useCallback(() => {
    if (sourceKey === '' || strategy === null) return
    void run.enqueue({
      keys: [sourceKey],
      variant: `${strategy}-${chunkSize}-${chunkOverlap}`,
    })
  }, [chunkOverlap, chunkSize, run, sourceKey, strategy])

  /**
   * Queue the same file under every strategy.
   *
   * Four requests rather than one, and they join a single run: the queue holds
   * each entry with its own way of cutting, so all four are embedded on their
   * own terms behind one progress bar.
   */
  const handleIndexAll = useCallback(() => {
    if (sourceKey === '') return
    for (const spec of preview.strategies) {
      void run.enqueue({
        keys: [sourceKey],
        variant: `${spec.id}-${chunkSize}-${chunkOverlap}`,
      })
    }
  }, [chunkOverlap, chunkSize, preview.strategies, run, sourceKey])

  const handleScore = useCallback(
    (setId: string, topK: number, generate: boolean) => {
      void score.start({ set_id: setId, top_k: topK, generate })
    },
    [score],
  )

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Chunking</h1>
          <p className="mt-1 text-sm text-slate-500">
            Cut one file several ways, embed each on its own, and see which one
            finds the right passage. Your normal index is not touched.
          </p>
        </div>
        <button
          type="button"
          onClick={variants.refresh}
          className="shrink-0 rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
        >
          Refresh
        </button>
      </header>

      <StrategyBench
        sources={sources.sources}
        strategies={preview.strategies}
        sourceKey={sourceKey}
        strategy={strategy}
        chunkSize={chunkSize}
        chunkOverlap={chunkOverlap}
        previewing={preview.loading}
        indexing={run.running}
        onSourceKey={setPickedSource}
        onStrategy={setPickedStrategy}
        onChunkSize={setChunkSize}
        onChunkOverlap={setChunkOverlap}
        onPreview={handlePreview}
        onIndex={handleIndex}
        onIndexAll={handleIndexAll}
      />

      {preview.error ? (
        <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {preview.error}
        </p>
      ) : null}

      {preview.preview ? <PreviewPanel preview={preview.preview} /> : null}

      <IndexProgress
        running={run.running}
        progress={run.progress}
        queued={run.queued}
        pending={run.pending}
        failures={run.failures}
        summary={run.summary}
        reused={run.reused}
        rejected={run.rejected}
        limit={run.limit}
        error={run.error}
        resumed={run.resumed}
        onStop={() => void run.stop()}
        onDismiss={run.reset}
      />

      {variants.error ? (
        <p className="mb-4 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {variants.error}
        </p>
      ) : null}

      <VariantsTable
        variants={variants.variants}
        loading={variants.loading}
        deleting={variants.deleting}
        onAsk={onAsk}
        onDelete={setDeleting}
      />

      <Scoreboard
        sets={golden.sets}
        sourceKey={sourceKey}
        run={score}
        onScore={handleScore}
      />

      <ConfirmDialog
        open={deleting !== null}
        title="Delete this variant?"
        confirmLabel="Delete it"
        onCancel={() => setDeleting(null)}
        onConfirm={() => {
          if (deleting) void variants.remove(deleting.variant_id)
          setDeleting(null)
        }}
      >
        <p>
          <span className="font-mono">{deleting?.label}</span> holds{' '}
          {deleting?.vector_count} vector(s).
        </p>
        <p className="mt-2">
          The file itself stays in storage — only this way of cutting it is
          removed, and indexing it again rebuilds it.
        </p>
      </ConfirmDialog>
    </div>
  )
}
