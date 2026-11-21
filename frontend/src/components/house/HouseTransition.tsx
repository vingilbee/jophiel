import { createContext, useCallback, useContext, useEffect, useRef, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

/**
 * Walking through a door: the chosen house's door swings in, its lamplight spreads from the doorway
 * to an ivory screen, the route changes at peak brightness and the light fades on the destination.
 *
 * The overlay lives in the signed-in layout (not on the House page) so it survives the route change.
 * One walk at a time: taps during a walk are ignored. Callers skip this under reduced motion or a
 * modified click and let the link navigate normally. If the route changes some other way during the
 * walk (browser Back, a redirect), that change wins: the light fades and the walk doesn't navigate.
 * Leaving the signed-in layout mid-walk (signing out) cancels the walk outright.
 */

interface HouseTransition {
  enter: (to: string, door: HTMLElement) => void
}

const Ctx = createContext<HouseTransition | null>(null)

const DOOR_MS = 340
const LIGHT_DELAY_MS = 200
const LIGHT_MS = 560
const FADE_MS = 420

export function HouseTransitionProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const overlay = useRef<HTMLDivElement>(null)
  const walking = useRef(false)
  const { key: locationKey } = useLocation()
  const currentKey = useRef(locationKey)
  useEffect(() => {
    currentKey.current = locationKey
  }, [locationKey])
  const mounted = useRef(true)
  const active = useRef<Animation | null>(null)
  useEffect(() => {
    mounted.current = true
    return () => {
      // Cancelling rejects `finished`, so a pending walk ends without navigating.
      mounted.current = false
      active.current?.cancel()
    }
  }, [])

  const enter = useCallback(
    (to: string, door: HTMLElement) => {
      const light = overlay.current
      if (walking.current || !light) return
      walking.current = true
      const startKey = currentKey.current

      const box = door.getBoundingClientRect()
      const x = box.left + box.width / 2
      const y = box.top + box.height * 0.55
      const far = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y))
      light.style.setProperty('--door-x', `${x}px`)
      light.style.setProperty('--door-y', `${y}px`)

      door.animate([{ transform: 'rotateY(0deg)' }, { transform: 'rotateY(-82deg)' }], {
        duration: DOOR_MS,
        easing: 'cubic-bezier(0.45, 0, 0.2, 1)',
        fill: 'forwards',
      })

      const start = Math.max(box.width * 0.45, 8)
      const spread = light.animate(
        [
          { opacity: 0, clipPath: `circle(${start}px at ${x}px ${y}px)` },
          { opacity: 1, clipPath: `circle(${start * 1.4}px at ${x}px ${y}px)`, offset: 0.2 },
          { opacity: 1, clipPath: `circle(${far}px at ${x}px ${y}px)` },
        ],
        { duration: LIGHT_MS, delay: LIGHT_DELAY_MS, easing: 'cubic-bezier(0.6, 0, 0.8, 0.4)', fill: 'forwards' },
      )
      active.current = spread

      const done = () => {
        spread.cancel()
        active.current = null
        walking.current = false
      }
      spread.finished
        .then(() => {
          if (!mounted.current) return done()
          if (currentKey.current === startKey) navigate(to)
          requestAnimationFrame(() => {
            if (!mounted.current) return done()
            const fade = light.animate([{ opacity: 1, clipPath: 'none' }, { opacity: 0, clipPath: 'none' }], {
              duration: FADE_MS,
              easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
              fill: 'forwards',
            })
            active.current = fade
            fade.finished.then(() => {
              fade.cancel()
              done()
            }, done)
          })
        }, done)
    },
    [navigate],
  )

  return (
    <Ctx.Provider value={{ enter }}>
      {children}
      <div ref={overlay} className="house-light" aria-hidden="true" />
    </Ctx.Provider>
  )
}

export function useHouseTransition(): HouseTransition | null {
  return useContext(Ctx)
}
