/**
 * Client-side rules about which files may be uploaded.
 *
 * These exist for instant feedback — the server enforces the same rules and is
 * the one that actually decides. Duplicated deliberately: a rejection that only
 * arrives after a round trip feels broken.
 */

/** Extensions the pipeline has an extractor for. Mirrors text_extraction.py. */
export const SUPPORTED_EXTENSIONS = ['.txt', '.md', '.markdown'] as const

/** The `accept` attribute for a file input, derived from the same list. */
export const ACCEPT_ATTRIBUTE = SUPPORTED_EXTENSIONS.join(',')

/** Whether the pipeline could read a file with this name. */
export function isSupportedFile(filename: string): boolean {
  const lower = filename.toLowerCase()
  return SUPPORTED_EXTENSIONS.some((extension) => lower.endsWith(extension))
}

/** The final path segment of an object key — what the user thinks of as the name. */
export function basename(sourceKey: string): string {
  return sourceKey.slice(sourceKey.lastIndexOf('/') + 1)
}

/** Human-readable reason a file cannot be uploaded, or null if it can. */
export function rejectionReason(file: File): string | null {
  if (!isSupportedFile(file.name)) {
    return `Only ${SUPPORTED_EXTENSIONS.join(', ')} files can be indexed.`
  }
  if (file.size === 0) return 'The file is empty.'
  return null
}
