/**
 * Renders a markdown pipe table as a real <table>.
 *
 * Deliberately not a markdown library: the extraction pipeline emits exactly
 * one shape — a GitHub-style pipe table — and a bounded parser for that shape
 * is smaller than any dependency and has no HTML injection surface, because
 * every cell lands in JSX as text.
 *
 * A line that does not parse as a table row is shown as plain text below the
 * table rather than dropped, so a malformed extraction stays inspectable.
 */

interface MarkdownTableProps {
  markdown: string
}

/** Split one pipe-table line into its cells. */
function cells(line: string): string[] {
  return line
    .replace(/^\s*\|/, '')
    .replace(/\|\s*$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

/** The `| --- | :---: |` line separating a header from its body. */
function isRule(line: string): boolean {
  const trimmed = line.trim()
  if (!trimmed.includes('-')) return false
  return cells(trimmed).every((cell) => /^:?-{2,}:?$/.test(cell))
}

export function MarkdownTable({ markdown }: MarkdownTableProps) {
  const lines = markdown.split('\n').filter((line) => line.trim() !== '')
  const rows = lines.filter((line) => line.trim().startsWith('|'))
  const leftovers = lines.filter((line) => !line.trim().startsWith('|'))

  if (rows.length === 0) {
    return <pre className="text-sm whitespace-pre-wrap text-slate-600">{markdown}</pre>
  }

  const hasHeader = rows.length > 1 && isRule(rows[1])
  const header = hasHeader ? cells(rows[0]) : null
  const body = (hasHeader ? rows.slice(2) : rows).filter((line) => !isRule(line))

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        {header ? (
          <thead>
            <tr>
              {header.map((cell, index) => (
                <th
                  key={index}
                  className="border border-slate-200 bg-slate-50 px-3 py-1.5 text-left font-medium text-slate-700"
                >
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {body.map((line, rowIndex) => (
            <tr key={rowIndex}>
              {cells(line).map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className="tabular border border-slate-200 px-3 py-1.5 text-slate-600"
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {leftovers.length > 0 ? (
        <p className="mt-2 text-xs whitespace-pre-wrap text-slate-400">
          {leftovers.join('\n')}
        </p>
      ) : null}
    </div>
  )
}
