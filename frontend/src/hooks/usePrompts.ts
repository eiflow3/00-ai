/**
 * Loads the prompts the pipeline is assembled from, and saves edits to them.
 *
 * These templates used to be constants in the backend, so changing the wording
 * that grounds every answer meant a code change. They are records now, and this
 * is the client half of that: read them, edit one, put it back.
 *
 * A save is reported back rather than thrown. The backend refuses a template it
 * could not render — a placeholder it has no value for, a required one dropped
 * — and that message belongs beside the editor that produced it, not in a
 * toast that outlives the mistake.
 */

import { useCallback, useEffect, useState } from 'react'

import { listPrompts, previewPrompts, resetPrompt, updatePrompt } from '../api/client'
import type { Prompt, PromptPreview, PromptPreviewRequest } from '../api/types'

export interface UsePromptsResult {
  prompts: Prompt[]
  loading: boolean
  /** The list could not be loaded at all. */
  error: string | null
  /** Id of the prompt currently being written, if any. */
  saving: string | null
  /**
   * Bumped after every successful write.
   *
   * The preview is a render of these same templates, so it has to be rebuilt
   * whenever one changes — and this is what tells it to.
   */
  version: number
  /** Save an override. Resolves to the reason it was refused, or null. */
  save: (promptId: string, template: string) => Promise<string | null>
  /** Restore a prompt to the text it ships with. */
  reset: (promptId: string) => Promise<string | null>
}

export function usePrompts(): UsePromptsResult {
  const [prompts, setPrompts] = useState<Prompt[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    const abort = new AbortController()

    listPrompts(abort.signal)
      .then((loaded) => {
        if (abort.signal.aborted) return
        setPrompts(loaded)
        setLoading(false)
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setError(cause instanceof Error ? cause.message : String(cause))
        setLoading(false)
      })

    return () => abort.abort()
  }, [])

  /** Run one write, folding the prompt it returns back into the list. */
  const write = useCallback(
    async (promptId: string, call: () => Promise<Prompt>): Promise<string | null> => {
      setSaving(promptId)
      try {
        const saved = await call()
        // Replaced rather than refetched: the response is the prompt as it now
        // stands, and a second round trip could only disagree with it.
        setPrompts((current) =>
          current.map((prompt) => (prompt.id === saved.id ? saved : prompt)),
        )
        setVersion((current) => current + 1)
        return null
      } catch (cause: unknown) {
        return cause instanceof Error ? cause.message : String(cause)
      } finally {
        setSaving(null)
      }
    },
    [],
  )

  const save = useCallback(
    (promptId: string, template: string) =>
      write(promptId, () => updatePrompt(promptId, template)),
    [write],
  )

  const reset = useCallback(
    (promptId: string) => write(promptId, () => resetPrompt(promptId)),
    [write],
  )

  return { prompts, loading, error, saving, version, save, reset }
}

export interface UsePromptPreviewResult {
  preview: PromptPreview | null
  loading: boolean
  error: string | null
}

/** What a rendered preview was rendered for, so a stale one can be told apart. */
interface RenderedPreview {
  key: string
  preview: PromptPreview | null
  error: string | null
}

/**
 * Renders the prompts in force into the messages a request would actually send.
 *
 * Rebuilt whenever `version` changes, so an edit is visible in its assembled
 * form the moment it is saved — which is the only form that matters, since a
 * chunk format is sent once per retrieved chunk rather than once.
 *
 * What came back is stored with the request that asked for it, so "loading" is
 * derived from the two disagreeing rather than tracked as its own flag. The
 * previous render stays on screen while the next one is fetched, which is what
 * makes nudging the chunk count read as a change rather than a reload.
 */
export function usePromptPreview(
  options: PromptPreviewRequest,
  version: number,
): UsePromptPreviewResult {
  const [rendered, setRendered] = useState<RenderedPreview | null>(null)

  const { query, chunk_count: chunkCount, grounded } = options
  const key = `${version}|${chunkCount}|${grounded}|${query}`

  useEffect(() => {
    const abort = new AbortController()

    previewPrompts({ query, chunk_count: chunkCount, grounded }, abort.signal)
      .then((preview) => {
        if (!abort.signal.aborted) setRendered({ key, preview, error: null })
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted) return
        setRendered({
          key,
          // Keep the last good render visible beneath the error: it is still
          // what the pipeline would send if the failure is on this side.
          preview: null,
          error: cause instanceof Error ? cause.message : String(cause),
        })
      })

    return () => abort.abort()
  }, [key, query, chunkCount, grounded])

  const current = rendered?.key === key
  return {
    preview: rendered?.preview ?? null,
    loading: !current,
    error: current ? rendered.error : null,
  }
}
