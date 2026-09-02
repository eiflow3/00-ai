/**
 * The chunking variants that currently hold vectors.
 *
 * Read from the index rather than kept in step by hand: what exists is
 * whatever holds vectors, so a restart, a run that died halfway or a namespace
 * deleted from the Pinecone console all show the truth on the next refresh.
 */

import { useCallback, useEffect, useState } from 'react'

import { deleteVariant, listVariants } from '../api/client'
import type { ChunkVariant } from '../api/types'

export interface UseVariantsResult {
  variants: ChunkVariant[]
  loading: boolean
  error: string | null
  /** The variant currently being deleted, so its row can show it. */
  deleting: string | null
  refresh: () => void
  remove: (variantId: string) => Promise<void>
}

/** What the last finished read produced, tagged with the request it answered. */
interface Settled {
  nonce: number
  variants: ChunkVariant[]
  error: string | null
}

const PENDING: Settled = { nonce: -1, variants: [], error: null }

export function useVariants(): UseVariantsResult {
  const [nonce, setNonce] = useState(0)
  const [settled, setSettled] = useState<Settled>(PENDING)
  const [deleting, setDeleting] = useState<string | null>(null)

  const refresh = useCallback(() => setNonce((current) => current + 1), [])

  useEffect(() => {
    const abort = new AbortController()

    listVariants(abort.signal)
      .then((rows) => {
        if (!abort.signal.aborted) setSettled({ nonce, variants: rows, error: null })
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setSettled({
          nonce,
          variants: [],
          error: cause instanceof Error ? cause.message : String(cause),
        })
      })

    return () => abort.abort()
  }, [nonce])

  const remove = useCallback(
    async (variantId: string) => {
      setDeleting(variantId)
      try {
        await deleteVariant(variantId)
        // Re-read rather than dropping the row locally: the index is what is
        // authoritative about a deletion, and it is one request away.
        refresh()
      } catch (cause: unknown) {
        setSettled((current) => ({
          ...current,
          error: cause instanceof Error ? cause.message : String(cause),
        }))
      } finally {
        setDeleting(null)
      }
    },
    [refresh],
  )

  // Derived rather than set inside the effect: a read is outstanding exactly
  // when the settled result belongs to an older request.
  return {
    variants: settled.variants,
    loading: settled.nonce !== nonce,
    error: settled.error,
    deleting,
    refresh,
    remove,
  }
}
