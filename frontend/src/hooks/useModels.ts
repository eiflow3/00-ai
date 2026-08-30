/**
 * Loads the provider/model options this deployment offers.
 *
 * The list comes from the backend rather than being hardcoded here: which
 * providers work depends on the credentials it holds, and a list baked into
 * the client drifts out of date the moment one is added or removed.
 */

import { useEffect, useState } from 'react'

import { listModels } from '../api/client'
import type { ModelOption } from '../api/types'

export interface UseModelsResult {
  options: ModelOption[]
  loading: boolean
  error: string | null
}

/** What the last settled request produced. */
interface Result {
  settled: boolean
  options: ModelOption[]
  error: string | null
}

const PENDING: Result = { settled: false, options: [], error: null }

export function useModels(): UseModelsResult {
  const [result, setResult] = useState<Result>(PENDING)

  useEffect(() => {
    const abort = new AbortController()

    listModels(abort.signal)
      .then((options) => {
        if (!abort.signal.aborted) setResult({ settled: true, options, error: null })
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setResult({
          settled: true,
          options: [],
          error: cause instanceof Error ? cause.message : String(cause),
        })
      })

    return () => abort.abort()
  }, [])

  return {
    options: result.options,
    // Derived, so nothing is set synchronously inside the effect.
    loading: !result.settled,
    error: result.error,
  }
}
