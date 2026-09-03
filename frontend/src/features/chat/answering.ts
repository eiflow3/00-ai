/**
 * How the space answering a question reads on screen.
 *
 * Production is a pointer rather than a fixed index, so naming it takes two
 * facts: that this is the default, and which cut it currently points at. Kept
 * apart from the picker so the picker file exports only a component.
 */

import type { ProductionSpace } from '../../api/types'

/** What the empty variant is called before the pointer has been read. */
export const PRODUCTION_LABEL = 'Production'

/** How the default option reads: the pointer, and where it points. */
export function productionLabel(production: ProductionSpace | null): string {
  if (production === null) return PRODUCTION_LABEL
  if (production.variant_id === '') return production.label
  return `${PRODUCTION_LABEL} · ${production.label}`
}
