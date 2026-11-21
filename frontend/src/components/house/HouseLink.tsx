import { useRef, type CSSProperties, type MouseEvent } from 'react'
import { Link } from 'react-router-dom'
import cottage from '../../assets/house/cottage.webp'
import door from '../../assets/house/door.svg'
import { useHouseTransition } from './HouseTransition'

/**
 * One house: the cottage on its cloud, its door and its timber sign, all one link.
 * The door is decoration inside the link, so each destination is a single focus stop.
 * Every house uses the shared cottage until each page gets its own drawing.
 */
export function HouseLink({
  to,
  label,
  place,
  style,
}: {
  to: string
  label: string
  /** CSS hook for this house's spot in the village. */
  place: string
  style?: CSSProperties
}) {
  const doorRef = useRef<HTMLImageElement>(null)
  const transition = useHouseTransition()

  function onClick(e: MouseEvent<HTMLAnchorElement>) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    if (!transition || !doorRef.current) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    e.preventDefault()
    transition.enter(to, doorRef.current)
  }

  return (
    <Link to={to} onClick={onClick} className={`house house--${place}`} style={style}>
      <span className="house-bob">
        <span className="house-art">
          <img className="house-cottage" src={cottage} alt="" draggable={false} />
          <span className="house-door">
            <img ref={doorRef} src={door} alt="" draggable={false} />
          </span>
        </span>
        <span className="house-sign">
          <span className="house-sign-board">{label}</span>
        </span>
      </span>
    </Link>
  )
}
