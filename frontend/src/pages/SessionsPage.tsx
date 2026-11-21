import { AppHeader } from '../components/AppHeader'
import { Button } from '../components/Button'
import { DesktopSessionPanel } from '../components/DesktopSessionPanel'
import { useDesktopSession } from '../hooks/useDesktopSession'

/**
 * Sessions (`/sessions`): her desktop and game sessions on their own page, reached from the House.
 * The panel draws nothing when desktop sessions are off on this server, so the page says so instead.
 */
export function SessionsPage() {
  const { enabled, capabilities, retryCapabilities } = useDesktopSession()
  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-[#F8FAFF] text-[#23314F] md:flex-row">
      <AppHeader />
      <main className="relative mx-auto w-full max-w-5xl flex-1 px-5 py-10 sm:px-8 lg:px-12">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-[-0.03em]">Sessions</h1>
          <p className="mt-2 max-w-xl text-sm text-[#7183A3]">
            Her time on her own desktop: who has the controls, what she plans, and how to stop her.
          </p>
        </div>
        {enabled ? (
          <DesktopSessionPanel />
        ) : capabilities === 'failed' ? (
          <div className="max-w-xl rounded-2xl border border-[#D9E8F6] bg-white p-5 text-sm text-[#7183A3]">
            <p>Couldn&rsquo;t reach the server to check on sessions.</p>
            <Button variant="outline" className="mt-3" onClick={retryCapabilities}>
              Try again
            </Button>
          </div>
        ) : capabilities === 'loaded' ? (
          <p className="max-w-xl rounded-2xl border border-[#D9E8F6] bg-white p-5 text-sm text-[#7183A3]">
            Desktop sessions aren&rsquo;t turned on for this server, so there&rsquo;s nothing to show here yet.
          </p>
        ) : null}
      </main>
    </div>
  )
}
