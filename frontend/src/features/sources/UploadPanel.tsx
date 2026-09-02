/**
 * The upload control on the sources screen: a drop zone and a file picker.
 *
 * Uploaded files are stored but not indexed, so the panel says so rather than
 * leaving the user to wonder why a new row reads "not indexed".
 */

import { useRef, useState } from 'react'
import type { DragEvent } from 'react'

import { ACCEPT_ATTRIBUTE } from './uploadRules'
import { Spinner } from '../../components/Spinner'
import type { UploadItem } from '../../hooks/useUpload'

interface UploadPanelProps {
  items: UploadItem[]
  uploading: boolean
  onUpload: (files: File[]) => void
  onDismiss: () => void
}

export function UploadPanel({ items, uploading, onUpload, onDismiss }: UploadPanelProps) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    onUpload([...event.dataTransfer.files])
  }

  const rejected = items.filter((item) => item.outcome === 'rejected')
  const done = items.filter((item) => item.outcome === 'done')

  return (
    <section className="mb-4">
      <div
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`rounded-lg border border-dashed px-6 py-6 text-center transition-colors ${
          dragging ? 'border-slate-400 bg-slate-100' : 'border-slate-200 bg-white'
        }`}
      >
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT_ATTRIBUTE}
          className="hidden"
          onChange={(event) => {
            onUpload([...(event.target.files ?? [])])
            // Reset so choosing the same file twice still fires a change.
            event.target.value = ''
          }}
        />

        {uploading ? (
          <p className="flex items-center justify-center gap-2 text-sm text-slate-500">
            <Spinner />
            Uploading…
          </p>
        ) : (
          <>
            <p className="text-sm text-slate-600">
              Drop <code className="font-mono text-xs">.txt</code>,{' '}
              <code className="font-mono text-xs">.md</code> or{' '}
              <code className="font-mono text-xs">.pdf</code> files here, or{' '}
              <button
                type="button"
                onClick={() => input.current?.click()}
                className="font-medium text-slate-900 underline underline-offset-2 hover:text-slate-600"
              >
                choose files
              </button>
            </p>
            <p className="mt-1 text-xs text-slate-400">
              Uploaded files are stored but not embedded — press Index when you are ready.
            </p>
          </>
        )}
      </div>

      {items.length > 0 && !uploading ? (
        <div className="mt-2 flex items-start justify-between gap-4 rounded-lg border border-slate-200 bg-white px-4 py-3">
          <div className="min-w-0">
            {done.length > 0 ? (
              <p className="text-xs text-state-current">
                {done.length} file{done.length === 1 ? '' : 's'} uploaded.
              </p>
            ) : null}
            {rejected.map((item) => (
              <p key={item.name} className="text-xs text-state-orphaned">
                <span className="font-mono">{item.name}</span> — {item.error}
              </p>
            ))}
          </div>
          <button
            type="button"
            onClick={onDismiss}
            className="shrink-0 text-xs font-medium text-slate-400 hover:text-slate-600"
          >
            Dismiss
          </button>
        </div>
      ) : null}
    </section>
  )
}
