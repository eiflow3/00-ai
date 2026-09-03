/**
 * Which vector space the app answers from, and moving it.
 *
 * Production is a pointer rather than a place, so this is a setting the screen
 * can change — adopt the variant that scored best and the next answer comes
 * from it, with nothing re-embedded.
 *
 * Read back from the server after every move, because the server is what
 * decides whether a space can answer at all: an empty namespace or a
 * half-embedded one is refused, and the refusal is the useful part.
 */

import { useCallback, useEffect, useState } from 'react'

import { getProduction, setProduction } from '../api/client'
import type { ProductionSpace } from '../api/types'

export interface UseProductionResult {
  production: ProductionSpace | null
  loading: boolean
  error: string | null
  /** The variant currently being adopted, so its row can show it. */
  pointing: string | null
  refresh: () => void
  pointAt: (variantId: string) => Promise<boolean>
}

/** What the last finished read produced, tagged with the request it answered. */
interface Settled {
  nonce: number
  production: ProductionSpace | null
  error: string | null
}

const PENDING: Settled = { nonce: -1, production: null, error: null }

export function useProduction(): UseProductionResult {
  const [nonce, setNonce] = useState(0)
  const [settled, setSettled] = useState<Settled>(PENDING)
  const [pointing, setPointing] = useState<string | null>(null)

  const refresh = useCallback(() => setNonce((current) => current + 1), [])

  useEffect(() => {
    const abort = new AbortController()

    getProduction(abort.signal)
      .then((space) => {
        if (!abort.signal.aborted) setSettled({ nonce, production: space, error: null })
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setSettled({
          nonce,
          production: null,
          error: cause instanceof Error ? cause.message : String(cause),
        })
      })

    return () => abort.abort()
  }, [nonce])

  const pointAt = useCallback(async (variantId: string) => {
    setPointing(variantId)
    try {
      const space = await setProduction(variantId)
      // The response is the new state, so there is nothing to re-read: a
      // second request here would only be a chance to disagree with it.
      setSettled((current) => ({ ...current, production: space, error: null }))
      return true
    } catch (cause: unknown) {
      setSettled((current) => ({
        ...current,
        error: cause instanceof Error ? cause.message : String(cause),
      }))
      return false
    } finally {
      setPointing(null)
    }
  }, [])

  // Derived rather than set inside the effect: a read is outstanding exactly
  // when the settled result belongs to an older request.
  return {
    production: settled.production,
    loading: settled.nonce !== nonce,
    error: settled.error,
    pointing,
    refresh,
    pointAt,
  }
}
