import type { ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  /** What the user can do about it, when there is something. */
  hint?: ReactNode
}

/** Shown in place of a table or list that has nothing to show. */
export function EmptyState({ title, hint }: EmptyStateProps) {
  return (
    <div className="rounded-lg border border-dashed border-slate-200 px-6 py-12 text-center">
      <p className="text-sm font-medium text-slate-600">{title}</p>
      {hint ? <p className="mt-1.5 text-sm text-slate-400">{hint}</p> : null}
    </div>
  )
}
