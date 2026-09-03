/**
 * Chooses which chunking answers the question, and whether a second one
 * answers it alongside.
 *
 * The default is production, so anyone not running an experiment sees the
 * screen behave exactly as it did before this existed. Production is a pointer
 * rather than a fixed index, so the default option names the space it points
 * at — otherwise "which cut am I talking to" would be answerable only by
 * leaving this screen.
 *
 * Comparing is limited to two on purpose. Four columns of prose is not
 * something a person reads; four strategies ranked by hit-rate is, and that
 * lives on the Chunking tab. This is for looking at *why* one of them lost.
 */

import { productionLabel } from './answering'
import type { ChunkVariant, ProductionSpace } from '../../api/types'

interface VariantPickerProps {
  variants: ChunkVariant[]
  /** Where production currently answers from, or null until it is read. */
  production: ProductionSpace | null
  /** Empty means production. */
  primary: string
  /** Null when nothing is being compared. */
  secondary: string | null
  disabled: boolean
  onPrimary: (variantId: string) => void
  onSecondary: (variantId: string | null) => void
}

export function VariantPicker({
  variants,
  production,
  primary,
  secondary,
  disabled,
  onPrimary,
  onSecondary,
}: VariantPickerProps) {
  const options = variants.filter((variant) => variant.variant_id !== primary)

  // Nothing embedded under a variant yet, so there is nothing to choose
  // between — but where the answers come from is still worth saying, because
  // production can be pointed at a space this screen never named.
  if (variants.length === 0) {
    return (
      <p className="mb-4 text-sm text-slate-500">
        Answering from{' '}
        <span className="font-medium text-slate-700">
          {productionLabel(production)}
        </span>
      </p>
    )
  }

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
          <option value="">{productionLabel(production)}</option>
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
