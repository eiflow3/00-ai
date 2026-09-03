/**
 * Loads the deployment's resolved governance policy.
 *
 * The mode pickers label their "server default" option from this rather than
 * hardcoding what the server was configured with — a deployment switched to
 * audit_only must not have every picker still claiming enforce.
 */

import { useEffect, useState } from 'react'

import { getGovernancePolicy } from '../api/client'
import type { GovernancePolicyView } from '../api/types'

export interface UseGovernancePolicyResult {
  /** Null while loading, or when the backend could not be reached. */
  policy: GovernancePolicyView | null
}

export function useGovernancePolicy(): UseGovernancePolicyResult {
  const [policy, setPolicy] = useState<GovernancePolicyView | null>(null)

  useEffect(() => {
    const abort = new AbortController()

    getGovernancePolicy(abort.signal)
      .then((view) => {
        if (!abort.signal.aborted) setPolicy(view)
      })
      .catch(() => {
        // The picker falls back to an unlabelled "server default" option; a
        // failed probe is not worth a banner of its own.
      })

    return () => abort.abort()
  }, [])

  return { policy }
}
