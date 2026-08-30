/**
 * Uploads a batch of files, tracking each one separately.
 *
 * Files are sent one at a time rather than in parallel: the batch is small, the
 * order stays predictable, and one rejection cannot bury the others.
 */

import { useCallback, useState } from 'react'

import { uploadSource } from '../api/client'
import type { SourceStatus } from '../api/types'
import { rejectionReason } from '../features/sources/uploadRules'

export type UploadOutcome = 'uploading' | 'done' | 'rejected'

/** One file's progress through a batch. */
export interface UploadItem {
  name: string
  outcome: UploadOutcome
  /** Why it was rejected, when it was. */
  error?: string
}

export interface UseUploadResult {
  items: UploadItem[]
  uploading: boolean
  /** Upload a batch; resolves once every file has settled. */
  upload: (files: File[], prefix?: string) => Promise<void>
  /** Clear the last batch's results. */
  reset: () => void
}

/**
 * @param onUploaded - Called with each stored file's status, so the caller can
 *   show it without re-listing.
 */
export function useUpload(onUploaded?: (status: SourceStatus) => void): UseUploadResult {
  const [items, setItems] = useState<UploadItem[]>([])
  const [uploading, setUploading] = useState(false)

  const upload = useCallback(
    async (files: File[], prefix = '') => {
      if (files.length === 0) return

      setUploading(true)
      setItems(files.map((file) => ({ name: file.name, outcome: 'uploading' as const })))

      /** Record one file's result by its position in the batch. */
      const settle = (index: number, outcome: UploadOutcome, error?: string) => {
        setItems((current) =>
          current.map((item, at) => (at === index ? { ...item, outcome, error } : item)),
        )
      }

      for (const [index, file] of files.entries()) {
        // Catch what we can locally so the user is not waiting on a round trip
        // to learn the file was never eligible.
        const reason = rejectionReason(file)
        if (reason) {
          settle(index, 'rejected', reason)
          continue
        }

        try {
          const response = await uploadSource(file, prefix)
          settle(index, 'done')
          onUploaded?.(response.status)
        } catch (cause: unknown) {
          settle(index, 'rejected', cause instanceof Error ? cause.message : String(cause))
        }
      }

      setUploading(false)
    },
    [onUploaded],
  )

  const reset = useCallback(() => setItems([]), [])

  return { items, uploading, upload, reset }
}
