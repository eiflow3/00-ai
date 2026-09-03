/**
 * The chunking screen: cut one file several ways and find out which is better.
 *
 * The order down the page is the order the work happens in. Choose a strategy
 * and preview it, which costs nothing. Index the ones worth embedding, each
 * into its own space. See what exists. Then score every variant against a
 * golden set, which is the only step that actually answers the question.
 *
 * Every variant's vectors live in their own namespace, so a query against one
 * cannot return another's chunks — indexing here can never disturb an existing
 * copy of a document. What it can change is which copy answers: production is a
 * pointer, and adopting the winner of a comparison is a setting rather than a
 * corpus rebuild. The banner at the top always says where that pointer is.
 */

import { useCallback, useState } from 'react'

import { PreviewPanel } from './PreviewPanel'
import { ProductionBanner } from './ProductionBanner'
import { Scoreboard } from './Scoreboard'
import { ALL_FILES, StrategyBench } from './StrategyBench'
import { VariantsTable } from './VariantsTable'
import type { ChunkStrategy, ChunkVariant } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { useChunkPreview } from '../../hooks/useChunkPreview'
import { useGoldenSets } from '../../hooks/useGoldenSets'
import { useIndexRun } from '../../hooks/useIndexRun'
import { useProduction } from '../../hooks/useProduction'
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
  /** A file the Sources screen sent over, to open the bench on. */
  openSource?: string
}

export function ChunkingView({ onAsk, openSource = '' }: ChunkingViewProps) {
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
  const production = useProduction()

  // A finished run has written new vectors, and the variants list is read from
  // the index — so it is only right again once the run has ended.
  const onSettled = useCallback(() => {
    variants.refresh()
    // A run writes vectors, and the banner reports how many the answering
    // space holds — so it is only right again once the run has ended.
    production.refresh()
  }, [production, variants])
  const run = useIndexRun(onSettled)

  // A pick wins, then whatever the Sources screen sent over, then the one
  // obvious choice — making somebody pick from a list of one teaches nothing.
  const sourceKey =
    pickedSource ??
    (openSource !== ''
      ? openSource
      : sources.sources.length === 1
        ? sources.sources[0].source_key
        : '')

  const strategy = pickedStrategy ?? preview.strategies[0]?.id ?? null

  const handlePreview = useCallback(() => {
    if (sourceKey === '' || strategy === null) return
    void preview.run(sourceKey, {
      strategy,
      chunk_size: chunkSize,
      chunk_overlap: chunkOverlap,
    })
  }, [chunkOverlap, chunkSize, preview, sourceKey, strategy])

  /**
   * Name the files a run should cover.
   *
   * The whole bucket is sent as *no* keys rather than as every key: with none
   * named the server compares storage against the target variant and picks
   * what is missing or stale there, which is the sweep the Sources screen used
   * to own — and it skips paying to re-embed what that variant already holds.
   */
  const filesFor = useCallback(
    (key: string) => (key === ALL_FILES ? {} : { keys: [key] }),
    [],
  )

  const handleIndex = useCallback(() => {
    if (sourceKey === '' || strategy === null) return
    void run.enqueue({
      ...filesFor(sourceKey),
      variant: `${strategy}-${chunkSize}-${chunkOverlap}`,
    })
  }, [chunkOverlap, chunkSize, filesFor, run, sourceKey, strategy])

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
        ...filesFor(sourceKey),
        variant: `${spec.id}-${chunkSize}-${chunkOverlap}`,
      })
    }
  }, [chunkOverlap, chunkSize, filesFor, preview.strategies, run, sourceKey])

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

      <ProductionBanner
        production={production.production}
        loading={production.loading}
        error={production.error}
        onReset={() => void production.pointAt('')}
      />

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
        active={production.production?.variant_id ?? null}
        pointing={production.pointing}
        onAsk={onAsk}
        onAdopt={(variantId) => void production.pointAt(variantId)}
        onDelete={setDeleting}
      />

      <Scoreboard
        sets={golden.sets}
        sourceKey={sourceKey === ALL_FILES ? '' : sourceKey}
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
