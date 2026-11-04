/** Jophiel's mark: a glyph on a soft gradient, in place of a portrait. Fills its parent circle. */
export function JophielMark({ className = '' }: { className?: string }) {
  return (
    <span
      role="img"
      aria-label="Jophiel"
      className={`flex h-full w-full items-center justify-center bg-gradient-to-br from-[#EAF6FF] via-[#F2EFFB] to-[#D9D6F3] text-[#527998] ${className}`}
    >
      <span aria-hidden="true" className="text-[0.55em] leading-none">✦</span>
    </span>
  )
}

/** A person's initial, for the profile corner. Fills its parent circle. */
export function InitialAvatar({ name }: { name?: string | null }) {
  const initial = name?.trim().charAt(0).toUpperCase() || '?'
  return (
    <span
      aria-hidden="true"
      className="flex h-full w-full items-center justify-center text-[13px] font-semibold text-[#527998]"
    >
      {initial}
    </span>
  )
}
