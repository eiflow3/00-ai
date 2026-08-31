/**
 * Loads the vocabulary an evaluation is written in.
 *
 * Fetched once and shared, because it is static: the verdicts and reason chips
 * come from the backend so that judgements made in different places can still
 * be counted together.
 */

import { useEffect, useState } from 'react'

import { listEvaluationOptions } from '../api/client'
import type { EvaluationOptions, EvaluationTarget, TagOption } from '../api/types'

const EMPTY: EvaluationOptions = { verdicts: [], tags: [], targets: [] }

export interface UseEvaluationOptionsResult {
  options: EvaluationOptions
  loading: boolean
  error: string | null
  /** The reason chips that can explain one stage's verdict. */
  tagsFor: (target: EvaluationTarget) => TagOption[]
  /** Look up a chip's label, for rendering a stored evaluation. */
  labelFor: (tagId: string) => string
}

export function useEvaluationOptions(): UseEvaluationOptionsResult {
  const [options, setOptions] = useState<EvaluationOptions>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const abort = new AbortController()

    listEvaluationOptions(abort.signal)
      .then((loaded) => {
        if (!abort.signal.aborted) {
          setOptions(loaded)
          setLoading(false)
        }
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setError(cause instanceof Error ? cause.message : String(cause))
        setLoading(false)
      })

    return () => abort.abort()
  }, [])

  return {
    options,
    loading,
    error,
    tagsFor: (target) => options.tags.filter((tag) => tag.target === target),
    // Falls back to the raw id so a chip retired from the catalog still renders
    // on the judgements that already carry it.
    labelFor: (tagId) => options.tags.find((tag) => tag.id === tagId)?.label ?? tagId,
  }
}
