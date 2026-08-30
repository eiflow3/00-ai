/**
 * Choice of which model answers.
 *
 * Options come from the backend, so this shows what is actually usable here
 * rather than every model that exists. An option the deployment cannot use is
 * shown disabled with the reason, which is more useful than hiding it — the
 * reason is usually a setting someone can go and fill in.
 */

import type { ModelOption } from '../../api/types'

interface ModelPickerProps {
  options: ModelOption[]
  /** The chosen option's model id, or null before anything is chosen. */
  selected: string | null
  onSelect: (option: ModelOption) => void
  disabled: boolean
}

export function ModelPicker({ options, selected, onSelect, disabled }: ModelPickerProps) {
  if (options.length === 0) return null

  const chosen = options.find((option) => option.model === selected)

  return (
    <div className="mb-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs font-medium text-slate-400">Model</span>

        {options.map((option) => (
          <button
            key={option.model}
            type="button"
            onClick={() => onSelect(option)}
            // A running stream belongs to the model that started it; switching
            // mid-answer would mislabel what produced the text on screen.
            disabled={disabled || !option.available}
            title={option.detail || undefined}
            className={`rounded-full px-3 py-1 text-xs font-medium disabled:cursor-not-allowed ${
              option.model === selected
                ? 'bg-slate-900 text-white'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200 disabled:bg-slate-50 disabled:text-slate-300'
            }`}
          >
            {option.provider_label}
            <span className="ml-1.5 opacity-60">{option.model_label}</span>
          </button>
        ))}
      </div>

      {/* A caveat on the chosen option is worth showing outright, not just on
          hover — it usually names a setting that needs filling in. */}
      {chosen?.detail ? (
        <p className="mt-1.5 text-xs text-state-stale">{chosen.detail}</p>
      ) : null}

      {chosen && !chosen.priced ? (
        <p className="mt-1.5 text-xs text-slate-400">
          This model has no pricing entry, so its cost reports as zero.
        </p>
      ) : null}
    </div>
  )
}
