// The pieces every log on the System page is made of: a timeline of entries (when on the left, what on the
// right), an entry that opens to show the whole record, and a quiet view of a raw record.
import type { ReactNode } from 'react'
import { ago, clock } from './format'

/** The timeline: when on the left, what happened on the right. */
export function Timeline({ empty, children }: { empty: string; children: ReactNode[] }) {
  if (children.length === 0) return <p className="mt-3 text-[14px] text-[#8C97A3]">{empty}</p>
  return <ol className="mt-3">{children}</ol>
}

/** One record. ``more`` is the rest of it, behind a disclosure, so the timeline stays readable. */
export function Entry({ at, meta, children, tone, more, moreLabel = 'Whole record', action }: {
  at?: string | null
  meta: ReactNode
  children?: ReactNode
  tone?: 'error' | 'muted'
  more?: ReactNode
  moreLabel?: string
  action?: ReactNode
}) {
  return (
    <li className="grid grid-cols-[56px_minmax(0,1fr)] gap-x-4 border-t border-[#12171B] py-4 first:border-t-0 sm:grid-cols-[72px_minmax(0,1fr)]">
      <time className="pt-px text-[12px] text-[#7D8790] tabular-nums" dateTime={at ?? undefined} title={at ? clock(at) : undefined}>
        {at ? ago(at) : ''}
      </time>
      <div className="min-w-0">
        <div className="flex items-baseline gap-3">
          <p className={`min-w-0 flex-1 text-[12.5px] ${tone === 'error' ? 'text-[#FF8A80]' : 'text-[color:var(--part)]'}`}>{meta}</p>
          {action}
        </div>
        {children && (
          <div className={`mt-1.5 max-w-[68ch] text-[15px] leading-relaxed break-words ${tone === 'muted' ? 'text-[#8C97A3]' : 'text-[#DDE3E8]'}`}>
            {children}
          </div>
        )}
        {more && (
          <details className="mt-2.5 text-[14px]">
            <summary className="cursor-pointer text-[#AEB7BF] hover:text-white">{moreLabel}</summary>
            <div className="mt-2 border-l border-[color:var(--part)]/40 pl-4 text-[#AEB7BF]">{more}</div>
          </details>
        )}
      </div>
    </li>
  )
}

/** Label / value lines inside an opened record. Empty values are left out, never filled in. */
export function Facts({ rows }: { rows: Record<string, ReactNode> }) {
  const shown = Object.entries(rows).filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== false)
  if (shown.length === 0) return null
  return (
    <dl className="grid grid-cols-[minmax(84px,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13.5px]">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-[#7D8790]">{k}</dt>
          <dd className="min-w-0 break-words text-[#C5CDD5]">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

/** A raw record, readable: strings as text, everything else as indented JSON, scrolled when long. */
export function Raw({ value, max = 320 }: { value: unknown; max?: number }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return (
    <pre
      className="overflow-auto rounded-md bg-[#0A0D10] p-3 text-[12px] leading-relaxed whitespace-pre-wrap break-words text-[#AEB7BF]"
      style={{ maxHeight: max }}
    >
      {text}
    </pre>
  )
}

/** A small text button in an entry's corner (open the trace, jump to the part). */
export function LinkButton({ onClick, children, label }: { onClick: () => void; children: ReactNode; label?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="shrink-0 rounded-full px-2 py-0.5 text-[12px] text-[#AEB7BF] ring-1 ring-[#1E252B] transition-colors hover:text-white hover:ring-[#3A444D]"
    >
      {children}
    </button>
  )
}
