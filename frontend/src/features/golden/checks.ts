/**
 * What each validator check means, in a person's words.
 *
 * Its own module rather than living beside the badge that uses it: a file that
 * exports both components and helpers loses fast refresh, and the label map is
 * wanted by the row editor's issue list too.
 */

/** Plain-language name for each check `golden_validator` can report. */
const CHECK_LABELS: Record<string, string> = {
  keys_verbatim: 'answer key not in the document',
  keys_in_section: 'answer key not in the cited section',
  numeric_grounded: 'figure not stated and not derivable',
  sections_exist: 'cites a section that does not exist',
  forbidden_grounded: 'the trap is not real, or the answer springs it',
  refusal_shape: 'type and must_refuse disagree',
  no_duplicates: 'near-duplicate of another question',
  self_check: 'the reference answer fails its own row',
}

/**
 * Turn a check name into something readable.
 *
 * Falls back to the raw name with its underscores opened out, so a check added
 * to the backend shows up legibly here before this map is updated.
 */
export function checkLabel(check: string): string {
  return CHECK_LABELS[check] ?? check.replace(/_/g, ' ')
}
