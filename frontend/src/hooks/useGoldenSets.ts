/**
 * Loads the generated golden sets, and records what a person decides about them.
 *
 * A golden set is the answer key every future eval score is measured against,
 * so the point of this screen is judgement, not browsing: each row arrives with
 * the validator's findings attached, and a person accepts, fixes or drops it.
 *
 * Two behaviours matter here.
 *
 * A row edit is reported back rather than thrown, like a prompt save — the
 * backend re-checks an edited row against the source document, and "your fix
 * did not work, here is why" belongs beside the row that produced it.
 *
 * A row returned by an edit replaces the one in the list rather than triggering
 * a refetch. The response *is* the row as it now stands, freshly re-checked, so
 * a second round trip could only disagree with it. Dropping a row is the one
 * exception: it renumbers every row after it, so the set is re-read.
 */

import { useCallback, useEffect, useState } from 'react'

import {
  deleteGoldenSet,
  getGoldenSet,
  listGoldenSets,
  renameGoldenSet,
  updateGoldenRow,
} from '../api/client'
import type { GoldenRow, GoldenRowUpdate, GoldenSet, GoldenSetDetail } from '../api/types'

export interface UseGoldenSetsResult {
  sets: GoldenSet[]
  /** The set being reviewed, with its rows. Null until one is opened. */
  selected: GoldenSetDetail | null
  loading: boolean
  /** True while the selected set's rows are being fetched. */
  loadingSet: boolean
  error: string | null
  /** Id of the row currently being written, if any. */
  saving: string | null
  refresh: () => void
  open: (setId: string) => void
  close: () => void
  /** Apply an edit or a decision. Resolves to the reason it was refused, or null. */
  updateRow: (rowId: string, update: GoldenRowUpdate) => Promise<string | null>
  rename: (slug: string) => Promise<string | null>
  remove: (setId: string) => Promise<string | null>
}

/** Turn any thrown value into a message worth showing. */
function reason(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause)
}

export function useGoldenSets(): UseGoldenSetsResult {
  const [sets, setSets] = useState<GoldenSet[]>([])
  const [detail, setDetail] = useState<GoldenSetDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const [openId, setOpenId] = useState<string | null>(null)

  useEffect(() => {
    const abort = new AbortController()

    listGoldenSets(abort.signal)
      .then((loaded) => {
        if (abort.signal.aborted) return
        setSets(loaded)
        setLoading(false)
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setError(reason(cause))
        setLoading(false)
      })

    return () => abort.abort()
  }, [version])

  useEffect(() => {
    if (openId === null) return

    const abort = new AbortController()

    getGoldenSet(openId, abort.signal)
      .then((loaded) => {
        if (abort.signal.aborted) return
        setDetail(loaded)
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setError(reason(cause))
      })

    return () => abort.abort()
  }, [openId, version])

  /**
   * What is actually on screen, derived rather than stored.
   *
   * Closing a set, or opening a different one, must stop showing the old rows
   * immediately — and clearing a piece of state from inside the effect that
   * fetches the new one would paint the stale rows once first.
   */
  const selected = openId !== null && detail?.set_id === openId ? detail : null

  // "A set is open but its rows have not arrived" — nothing a flag could say
  // that this does not, and one fewer piece of state to keep honest.
  const loadingSet = openId !== null && selected === null && error === null

  const refresh = useCallback(() => setVersion((current) => current + 1), [])

  const updateRow = useCallback(
    async (rowId: string, update: GoldenRowUpdate): Promise<string | null> => {
      if (selected === null) return 'No set is open.'

      setSaving(rowId)
      try {
        const saved: GoldenRow = await updateGoldenRow(selected.set_id, rowId, update)

        // A drop renumbers everything after it, so the whole set is re-read.
        // Any other edit returns the row as it now stands, already re-checked.
        if (update.review === 'dropped') {
          refresh()
          return null
        }

        setDetail((current) =>
          current === null
            ? current
            : {
                ...current,
                rows: current.rows.map((row) => (row.row_id === saved.row_id ? saved : row)),
              },
        )
        return null
      } catch (cause: unknown) {
        return reason(cause)
      } finally {
        setSaving(null)
      }
    },
    [refresh, selected],
  )

  const rename = useCallback(
    async (slug: string): Promise<string | null> => {
      if (selected === null) return 'No set is open.'
      try {
        const renamed = await renameGoldenSet(selected.set_id, slug)
        setDetail(renamed)
        setSets((current) =>
          current.map((set) =>
            set.set_id === renamed.set_id ? { ...set, slug: renamed.slug } : set,
          ),
        )
        return null
      } catch (cause: unknown) {
        return reason(cause)
      }
    },
    [selected],
  )

  const remove = useCallback(
    async (setId: string): Promise<string | null> => {
      try {
        await deleteGoldenSet(setId)
        if (setId === openId) setOpenId(null)
        refresh()
        return null
      } catch (cause: unknown) {
        return reason(cause)
      }
    },
    [openId, refresh],
  )

  return {
    sets,
    selected,
    loading,
    loadingSet,
    error,
    saving,
    refresh,
    open: setOpenId,
    close: () => setOpenId(null),
    updateRow,
    rename,
    remove,
  }
}
