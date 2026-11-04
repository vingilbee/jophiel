import paleBlueFeather from '../assets/pale-blue-feather.svg'

/** Public-domain feather ornament used as non-interactive atmosphere. */
export function Feathers({ className = '' }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none select-none ${className}`}
    >
      <img
        src={paleBlueFeather}
        alt=""
        className="absolute inset-0 h-full w-full object-contain opacity-90"
      />
      <img
        src={paleBlueFeather}
        alt=""
        className="absolute inset-0 h-full w-full scale-[.78] -rotate-[18deg] object-contain opacity-45"
      />
    </div>
  )
}
