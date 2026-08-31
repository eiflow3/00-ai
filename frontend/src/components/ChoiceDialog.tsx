/**
 * A modal that asks *which* action, not merely whether.
 *
 * ConfirmDialog covers the yes-or-no case. This one exists for actions where
 * the dangerous reading and the safe reading are both plausible — deleting a
 * file versus only its embeddings — so offering a single "Continue" would make
 * the user guess which one they were about to get.
 *
 * The choices are stacked rather than laid out in a row: each carries its own
 * consequence line, and a row of equally-sized buttons invites a click before
 * that line is read.
 *
 * Built on the native <dialog>, which brings the focus trap, the Escape key and
 * the backdrop for free.
 */

import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

export interface DialogChoice {
  label: string
  /** What this choice actually does, in the user's own terms. */
  description?: string
  /** True for the irreversible one, which is tinted rather than hidden. */
  danger?: boolean
  /** Offered but unavailable — the reason belongs in `description`. */
  disabled?: boolean
  onSelect: () => void
}

interface ChoiceDialogProps {
  open: boolean
  title: string
  /** The specifics of what is being acted on. */
  children: ReactNode
  choices: DialogChoice[]
  cancelLabel?: string
  onCancel: () => void
}

export function ChoiceDialog({
  open,
  title,
  children,
  choices,
  cancelLabel = 'Cancel',
  onCancel,
}: ChoiceDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const element = dialog.current
    if (!element) return

    // showModal is what makes it modal — rendering the element is not enough.
    if (open && !element.open) element.showModal()
    if (!open && element.open) element.close()
  }, [open])

  return (
    <dialog
      ref={dialog}
      // Fires for Escape and the close button alike, so cancelling is handled
      // in one place rather than per dismissal route.
      onClose={onCancel}
      onClick={(event) => {
        // A click landing on the dialog itself is a click on the backdrop;
        // anything inside hits a child instead.
        if (event.target === dialog.current) onCancel()
      }}
      className="m-auto w-full max-w-md rounded-lg border border-slate-200 p-0 backdrop:bg-slate-900/40"
    >
      <div className="p-5">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        <div className="mt-2 text-sm text-slate-600">{children}</div>

        <div className="mt-4 space-y-2">
          {choices.map((choice) => (
            <button
              key={choice.label}
              type="button"
              onClick={choice.onSelect}
              disabled={choice.disabled}
              className={`w-full rounded-md border px-3 py-2 text-left disabled:opacity-40 ${
                choice.danger
                  ? 'border-state-orphaned-soft bg-state-orphaned-soft text-state-orphaned enabled:hover:brightness-95'
                  : 'border-slate-200 text-slate-700 enabled:hover:bg-slate-100'
              }`}
            >
              <span className="block text-sm font-medium">{choice.label}</span>
              {choice.description ? (
                <span className="mt-0.5 block text-xs opacity-80">
                  {choice.description}
                </span>
              ) : null}
            </button>
          ))}
        </div>

        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            {cancelLabel}
          </button>
        </div>
      </div>
    </dialog>
  )
}
