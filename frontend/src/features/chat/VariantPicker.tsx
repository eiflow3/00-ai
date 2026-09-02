/**
 * Chooses which chunking answers the question, and whether a second one
 * answers it alongside.
 *
 * The default is the production index, so anyone not running an experiment
 * sees the screen behave exactly as it did before this existed.
 *
 * Comparing is limited to two on purpose. Four columns of prose is not
 * something a person reads; four strategies ranked by hit-rate is, and that
 * lives on the Chunking tab. This is for looking at *why* one of them lost.
 */

import type { ChunkVariant } from '../../api/types'

/** What the empty variant is called on screen. */
export const PRODUCTION_LABEL = 'Production index'

interface VariantPickerProps {
  variants: ChunkVariant[]
  /** Empty means the production index. */
  primary: string
  /** Null when nothing is being compared. */
  secondary: string | null
  disabled: boolean
  onPrimary: (variantId: string) => void
  onSecondary: (variantId: string | null) => void
}

export function VariantPicker({
  variants,
  primary,
  secondary,
  disabled,
  onPrimary,
  onSecondary,
}: VariantPickerProps) {
  // Nothing embedded under a variant yet, so there is nothing to choose
  // between. Showing a picker with one option would be noise.
  if (variants.length === 0) return null

  const options = variants.filter((variant) => variant.variant_id !== primary)

  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 text-sm">
      <label className="flex items-center gap-2">
        <span className="text-slate-500">Answer from</span>
        <select
          value={primary}
          onChange={(event) => onPrimary(event.target.value)}
          disabled={disabled}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="">{PRODUCTION_LABEL}</option>
          {variants.map((variant) => (
            <option key={variant.variant_id} value={variant.variant_id}>
              {variant.label}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={secondary !== null}
          disabled={disabled || options.length === 0}
          onChange={(event) =>
            onSecondary(event.target.checked ? (options[0]?.variant_id ?? null) : null)
          }
        />
        <span className="text-slate-500">Compare with</span>
      </label>

      {secondary !== null ? (
        <select
          value={secondary}
          onChange={(event) => onSecondary(event.target.value)}
          disabled={disabled}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          {options.map((variant) => (
            <option key={variant.variant_id} value={variant.variant_id}>
              {variant.label}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  )
}
