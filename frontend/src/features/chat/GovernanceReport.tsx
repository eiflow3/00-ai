/**
 * What governance did to this exchange, in one quiet line per screening.
 *
 * Deliberately understated when nothing happened — "screened, no findings"
 * is reassurance, not news — and unmissable when the request ran unscreened
 * or was refused: those are the two outcomes a person must not misread.
 */

import type { BlockedEventData, GovernanceEventData } from '../../api/types'

/** "2× email (personal, masked)" — a finding line a person can read. */
function findingLine(event: GovernanceEventData | BlockedEventData): string {
  return event.findings
    .map((finding) => {
      const kind = finding.entity_type.replace(/_/g, ' ')
      const clause = [finding.classification, finding.action]
        .filter(Boolean)
        .join(', ')
      return `${finding.count}× ${kind}${clause ? ` (${clause})` : ''}`
    })
    .join(' · ')
}

const POINT_LABEL: Record<GovernanceEventData['point'], string> = {
  inbound: 'Question',
  outbound: 'Answer',
}

interface GovernanceReportProps {
  governance: GovernanceEventData[]
  blocked: BlockedEventData | null
}

export function GovernanceReport({ governance, blocked }: GovernanceReportProps) {
  if (governance.length === 0 && !blocked) return null

  return (
    <div className="mb-4">
      {blocked ? (
        <p className="mb-2 rounded-lg border border-state-orphaned-soft bg-state-orphaned-soft px-4 py-3 text-sm text-state-orphaned">
          {blocked.message}
          {blocked.findings.length > 0 ? (
            <span className="mt-1 block text-xs opacity-80">{findingLine(blocked)}</span>
          ) : null}
        </p>
      ) : null}

      {governance.length > 0 ? (
        <ul className="space-y-0.5 text-xs text-slate-400">
          {governance.map((event) => (
            <li key={event.point}>
              <span className="font-medium text-slate-500">
                {POINT_LABEL[event.point]}:
              </span>{' '}
              {!event.screened ? (
                <span className="text-state-stale">
                  not screened — governance was off
                </span>
              ) : event.findings.length === 0 ? (
                'screened, no findings'
              ) : (
                findingLine(event)
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
