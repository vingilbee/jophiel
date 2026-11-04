import { useMemo } from 'react'
import type { PartState } from '../../types/system'
import type { PartAnatomy } from './anatomy'
import { PERIOD, FAIL } from './nodeStates'
import { filaments, seedOf } from './shapes'

/** One part drawn as on the map (core, charged ring, filaments), at a fixed size, beating with its state. */
export function NodeGlyph({ part, state, size = 168 }: { part: PartAnatomy; state: PartState | undefined; size?: number }) {
  const r = 40
  const fils = useMemo(() => filaments(r, seedOf(part.id)).paths, [part.id])
  const period = state ? PERIOD[state] : undefined
  const dim = state === 'down' ? 0.3 : state === 'off' ? 0.16 : state === undefined ? 0.45 : state === 'partial' ? 0.75 : 1
  const ring = state === 'failing' ? FAIL : part.color
  const pace = { ['--period' as string]: `${period ?? 3}s`, ['--delay' as string]: '0s' }
  const id = `glyph-${part.id}`
  return (
    <svg width={size} height={size} viewBox="-100 -100 200 200" aria-hidden="true" className="overflow-visible">
      <defs>
        <radialGradient id={`${id}-core`}>
          <stop offset="0%" stopColor="#FFFFFF" />
          <stop offset="38%" stopColor="#F4FFFE" />
          <stop offset="62%" stopColor={part.color} />
          <stop offset="100%" stopColor={part.color} stopOpacity="0.85" />
        </radialGradient>
        <radialGradient id={`${id}-halo`}>
          <stop offset="0%" stopColor={part.color} stopOpacity="0.55" />
          <stop offset="40%" stopColor={part.color} stopOpacity="0.16" />
          <stop offset="100%" stopColor={part.color} stopOpacity="0" />
        </radialGradient>
      </defs>
      <g opacity={dim}>
        <circle r={r * 2.4} fill={`url(#${id}-halo)`} />
        {period && <circle className="ca" r={r * 2.2} fill={`url(#${id}-halo)`} style={{ ...pace, ['--peak' as string]: 1 }} />}
        <g className={period ? 'flicker' : undefined}>
          {fils.map((d, i) => (
            <path key={i} d={d} fill="none" stroke={part.color} strokeWidth={1.4} strokeOpacity={0.8} strokeLinecap="round" strokeLinejoin="round" />
          ))}
        </g>
        <circle r={r * 0.8} fill="none" stroke={ring} strokeWidth={1.4} strokeOpacity={0.3} />
        <circle
          r={r * 0.8}
          fill="none"
          stroke={ring}
          strokeWidth={state === 'failing' ? 3 : 2.4}
          strokeDasharray={state === 'partial' ? '16 10' : state === 'off' ? '4 6' : undefined}
          className={period ? (state === 'failing' ? 'fail-rim' : 'charge') : undefined}
          style={period ? pace : undefined}
          opacity={period ? undefined : 0.35}
        />
        <circle r={r * 0.7} fill={`url(#${id}-halo)`} />
        {state !== 'off' && <circle r={r * 0.36} fill={`url(#${id}-core)`} />}
        {period && <circle className="ca" r={r * 0.42} fill="#FFFFFF" style={{ ...pace, ['--peak' as string]: 0.7 }} />}
      </g>
    </svg>
  )
}
