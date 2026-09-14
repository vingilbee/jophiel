import type { ButtonHTMLAttributes } from 'react'

type Variant = 'primary' | 'ghost' | 'outline'

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-accent text-ink hover:brightness-110 shadow-[0_0_20px_-6px_var(--color-accent)]',
  outline:
    'border border-line-bright bg-surface-2 text-fg hover:border-accent hover:text-accent',
  ghost: 'text-muted hover:text-fg',
}

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
}

export function Button({ variant = 'primary', className = '', ...props }: Props) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm font-medium transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  )
}
