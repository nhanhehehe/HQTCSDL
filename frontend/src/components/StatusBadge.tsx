export type BadgeTone = 'ok' | 'warn' | 'err' | 'neutral'

export function StatusBadge({
  tone = 'neutral',
  label,
  dot = true,
}: {
  tone?: BadgeTone
  label: string
  dot?: boolean
}) {
  return (
    <span className={`badge badge-${tone}`}>
      {dot && <span className="badge-dot" aria-hidden="true" />}
      <span>{label}</span>
    </span>
  )
}
