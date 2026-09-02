/**
 * Previews how a strategy would cut a file, and the catalog of strategies.
 *
 * Both in one hook because the screen needs them together and neither is worth
 * its own: the catalog decides what can be picked, and a preview is what
 * picking produces.
 *
 * A preview costs nothing — no embedding, no write — which is the whole reason
 * it exists as a separate step from indexing.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { listStrategies, previewChunking } from '../api/client'
import type { ChunkPreviewResponse, ChunkStrategySpec, ChunkingConfig } from '../api/types'

export interface UseChunkPreviewResult {
  strategies: ChunkStrategySpec[]
  /** The last preview produced, or null before one has been asked for. */
  preview: ChunkPreviewResponse | null
  loading: boolean
  error: string | null
  run: (sourceKey: string, config: ChunkingConfig) => Promise<void>
  clear: () => void
}

export function useChunkPreview(): UseChunkPreviewResult {
  const [strategies, setStrategies] = useState<ChunkStrategySpec[]>([])
  const [preview, setPreview] = useState<ChunkPreviewResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const controller = useRef<AbortController | null>(null)

  useEffect(() => {
    const abort = new AbortController()

    listStrategies(abort.signal)
      .then((specs) => {
        if (!abort.signal.aborted) setStrategies(specs)
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted) {
          setError(cause instanceof Error ? cause.message : String(cause))
        }
      })

    return () => abort.abort()
  }, [])

  const run = useCallback(async (sourceKey: string, config: ChunkingConfig) => {
    // Previewing again abandons the previous one: only the latest answer is
    // the one on screen, and a slow earlier request must not overwrite it.
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort

    setLoading(true)
    setError(null)

    try {
      const result = await previewChunking(sourceKey, config, abort.signal)
      if (!abort.signal.aborted) setPreview(result)
    } catch (cause: unknown) {
      if (!abort.signal.aborted) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    } finally {
      if (controller.current === abort) setLoading(false)
    }
  }, [])

  const clear = useCallback(() => {
    controller.current?.abort()
    setPreview(null)
    setError(null)
  }, [])

  return { strategies, preview, loading, error, run, clear }
}
