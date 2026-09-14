import { Link } from 'react-router-dom'

export function Logo({ to = '/' }: { to?: string }) {
  return (
    <Link to={to} className="group flex items-center gap-2.5">
      <span className="relative flex h-7 w-7 items-center justify-center rounded-md border border-line-bright bg-surface-2">
        <span className="h-2.5 w-2.5 rounded-sm bg-accent shadow-[0_0_12px_var(--color-accent)]" />
      </span>
      <span className="font-mono text-sm tracking-[0.2em] text-fg uppercase">
        Macro<span className="text-accent">Vision</span>
      </span>
    </Link>
  )
}
