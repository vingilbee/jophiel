import { Link } from 'react-router-dom'

export function Logo({ to = '/' }: { to?: string }) {
  return (
    <Link to={to} className="group flex items-center gap-2.5">
      <span className="relative flex h-7 w-7 items-center justify-center rounded-full border border-[#B9DCF4] bg-[#F2EFFB] text-[#8B3158]">
        ✦
      </span>
      <span className="text-sm font-semibold tracking-[0.04em] text-[#23314F]">
        Jophiel
      </span>
    </Link>
  )
}
