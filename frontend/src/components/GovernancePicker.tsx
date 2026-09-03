/**
 * The governance mode knob, shared by chat and indexing.
 *
 * Empty string means "send nothing" — the server's configured default
 * applies, and the option says which mode that is so choosing it is an
 * informed act rather than a shrug. Mode is the only governance field a
 * request may override; everything else is the operator's.
 */

import { useGovernancePolicy } from '../hooks/useGovernancePolicy'
import type { GovernanceMode } from '../api/types'

/** Human wording for each mode, matched to what it actually does. */
const MODE_LABEL: Record<GovernanceMode, string> = {
  off: 'Off — no screening',
  audit_only: 'Audit only — record, change nothing',
  enforce: 'Enforce — redact before use',
}

interface GovernancePickerProps {
  /** The pick, or '' for the server default. */
  value: GovernanceMode | ''
  onChange: (mode: GovernanceMode | '') => void
  disabled?: boolean
}

export function GovernancePicker({ value, onChange, disabled }: GovernancePickerProps) {
  const { policy } = useGovernancePolicy()

  const defaultLabel = policy
    ? `Server default (${MODE_LABEL[policy.mode]})`
    : 'Server default'

  return (
    <label>
      <span className="mb-1 block text-xs font-medium text-slate-600">Governance</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as GovernanceMode | '')}
        disabled={disabled}
        className="rounded border border-slate-300 px-2 py-1.5 text-sm disabled:bg-slate-50 disabled:text-slate-400"
      >
        <option value="">{defaultLabel}</option>
        <option value="enforce">{MODE_LABEL.enforce}</option>
        <option value="audit_only">{MODE_LABEL.audit_only}</option>
        <option value="off">{MODE_LABEL.off}</option>
      </select>
    </label>
  )
}
