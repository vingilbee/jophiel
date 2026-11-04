// The System page's few icons, drawn in one stroke weight.
const base = {
  width: 16,
  height: 16,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
}

export const PlusIcon = () => (
  <svg {...base}>
    <path d="M8 3.5v9M3.5 8h9" />
  </svg>
)

export const MinusIcon = () => (
  <svg {...base}>
    <path d="M3.5 8h9" />
  </svg>
)

/** Fit: four corners drawing in. */
export const FitIcon = () => (
  <svg {...base}>
    <path d="M3 6V3h3M10 3h3v3M13 10v3h-3M6 13H3v-3" />
  </svg>
)

export const CloseIcon = () => (
  <svg {...base}>
    <path d="M4 4l8 8M12 4l-8 8" />
  </svg>
)
