/**
 * A modal confirmation, used where an action is probably a mistake but might
 * not be — so it warns rather than refusing.
 *
 * Built on the native <dialog>, which brings the focus trap, the Escape key and
 * the backdrop for free.
 */

import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  /** The specifics — what will happen, in the user's own terms. */
  children: ReactNode
  confirmLabel?: string
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = 'Continue',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
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

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  )
}
