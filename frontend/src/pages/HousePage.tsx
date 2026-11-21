import { useNavigate } from 'react-router-dom'
import { CloudBed } from '../components/house/CloudBed'
import { HouseLink } from '../components/house/HouseLink'
import { Sky } from '../components/house/Sky'
import { useAuth } from '../hooks/useAuth'
import '../components/house/house.css'

/**
 * The House (`/dashboard`): the signed-in home and the way to every page. A village of cottages on
 * clouds in a golden-dusk sky; each cottage is a link, and going in plays the door transition
 * (`HouseTransition`). System is shown to the owner only (its route stays guarded either way).
 */

/** In reading and focus order: on phones this is also the visual order. */
const HOUSES = [
  { to: '/chat', label: 'Chat', place: 'chat' },
  { to: '/integrations', label: 'Integrations', place: 'integrations' },
  { to: '/sessions', label: 'Sessions', place: 'sessions' },
  { to: '/system', label: 'System', place: 'system', ownerOnly: true },
] as const

export function HousePage() {
  const { user, signout } = useAuth()
  const navigate = useNavigate()
  const owner = Boolean(user?.is_owner)
  const houses = HOUSES.filter((h) => !('ownerOnly' in h) || owner)

  async function handleSignout() {
    await signout()
    navigate('/')
  }

  return (
    <main className={`house-page ${owner ? 'house-page--owner' : ''}`}>
      <Sky />
      <h1 className="sr-only">Home</h1>

      <button type="button" className="house-cloud-button" onClick={handleSignout}>
        <svg viewBox="0 0 160 64" aria-hidden="true">
          <path d="M28 58C6 58 4 34 24 30Q30 10 52 16Q68 2 88 12Q110 2 126 18Q150 16 150 38Q156 58 132 58Z" />
        </svg>
        <span>Sign out</span>
      </button>

      <div className="house-stage">
        <nav className="house-village" aria-label="Places">
          {houses.map((h) => (
            <HouseLink key={h.to} to={h.to} label={h.label} place={h.place} />
          ))}
        </nav>
        <CloudBed />
      </div>
    </main>
  )
}
