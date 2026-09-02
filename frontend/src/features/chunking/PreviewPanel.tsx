/**
 * What a strategy did to the document, before a penny is spent embedding it.
 *
 * The bar strip is the part worth having. Four strategies at the same nominal
 * chunk size produce visibly different shapes — fixed gives a row of identical
 * bars, structural gives a ragged one — and that difference decides more about
 * retrieval than anything readable in the text of any single chunk.
 *
 * The chunk list is underneath rather than above, because it is the slow way to
 * learn the same thing.
 */

import { useState } from 'react'

import type { ChunkPreviewResponse } from '../../api/types'

interface PreviewPanelProps {
  preview: ChunkPreviewResponse
}

/** Chunks listed before the "show the rest" control appears. */
const VISIBLE_CHUNKS = 6

/** Shortest a bar may be drawn, so a tiny chunk is still visible as one. */
const MIN_BAR_PERCENT = 4

export function PreviewPanel({ preview }: PreviewPanelProps) {
  const [expanded, setExpanded] = useState(false)
  // Chunks the person has opened in full. A chunk is a few hundred tokens, so
  // showing every one of them whole turns the panel into a copy of the
  // document — which is not what anyone came here to read.
  const [opened, setOpened] = useState<number[]>([])

  const { stats, chunks } = preview
  const shown = expanded ? chunks : chunks.slice(0, VISIBLE_CHUNKS)

  function toggle(index: number) {
    setOpened((current) =>
      current.includes(index)
        ? current.filter((seen) => seen !== index)
        : [...current, index],
    )
  }

  return (
    <section className="mb-6 rounded-lg border border-slate-200 bg-white px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">
          {preview.label}
          <span className="ml-2 font-normal text-slate-400">
            nothing embedded, nothing charged
          </span>
        </h2>
        <p className="tabular text-xs text-slate-500">
          {stats.chunk_count} chunks · smallest {stats.min_tokens} · median{' '}
          {stats.median_tokens} · largest {stats.max_tokens} tokens ·{' '}
          {Math.round(stats.repeated_fraction * 100)}% repeated across boundaries
        </p>
      </div>

      {/* One bar per chunk, width proportional to its size. Reading the shape
          of a cut takes a second here and a scroll in the list below. */}
      <div className="mt-3 flex h-8 items-end gap-px overflow-x-auto">
        {chunks.map((chunk) => (
          <span
            key={chunk.chunk_index}
            title={`#${chunk.chunk_index} — ${chunk.token_count} tokens${
              chunk.note ? ` — ${chunk.note}` : ''
            }`}
            style={{
              height: `${Math.max(
                MIN_BAR_PERCENT,
                (chunk.token_count / Math.max(stats.max_tokens, 1)) * 100,
              )}%`,
            }}
            className="w-2 shrink-0 rounded-sm bg-slate-300"
          />
        ))}
      </div>

      {chunks.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">
          This file has no readable text, so there is nothing to embed.
        </p>
      ) : (
        <ol className="mt-3 divide-y divide-slate-100 border-t border-slate-100">
          {shown.map((chunk) => (
            <li key={chunk.chunk_index} className="flex gap-4 py-3">
              <div className="w-44 shrink-0">
                <p className="tabular font-mono text-xs text-slate-400">
                  #{String(chunk.chunk_index).padStart(2, '0')} · {chunk.token_count} tok
                </p>
                {chunk.note ? (
                  <p className="mt-0.5 text-xs leading-snug text-slate-400">{chunk.note}</p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => toggle(chunk.chunk_index)}
                title={opened.includes(chunk.chunk_index) ? 'Collapse' : 'Show the whole chunk'}
                className={`min-w-0 flex-1 cursor-pointer text-left text-sm whitespace-pre-wrap text-slate-600 ${
                  opened.includes(chunk.chunk_index) ? '' : 'line-clamp-3'
                }`}
              >
                {chunk.content}
              </button>
            </li>
          ))}
        </ol>
      )}

      {chunks.length > VISIBLE_CHUNKS ? (
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          className="mt-3 text-xs font-medium text-slate-500 hover:text-slate-700"
        >
          {expanded
            ? 'Show fewer'
            : `Show all ${chunks.length} chunks`}
        </button>
      ) : null}
    </section>
  )
}
