import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AppHeader } from '../components/AppHeader'
import { Button } from '../components/Button'
import { ProviderIcon } from '../components/ProviderIcon'
import { integrationsApi } from '../api/integrations'
import type { Integration } from '../types'

/**
 * Why a connection attempt failed, in the user's terms. Keys are the `reason`
 * codes the backend puts on its callback redirect.
 */
const FAILURE_REASONS: Record<string, string> = {
  access_denied: 'You declined the permission request.',
  not_signed_in: 'Your session expired while connecting. Sign in and try again.',
  state_mismatch:
    'That connection link did not match your session. Start the connection again.',
  exchange_failed: 'The provider rejected the connection. Please try again.',
  no_refresh_token:
    'The provider did not return a lasting token. Remove this app from your account settings, then reconnect.',
  insufficient_scope:
    'Some required permissions were not granted. Reconnect and accept all of them.',
  server_misconfigured: 'This server is not set up for that integration yet.',
  missing_code: 'The provider sent an incomplete response. Please try again.',
}

type Banner = { kind: 'success' | 'error'; text: string }

export function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [banner, setBanner] = useState<Banner | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()

  const load = useCallback(async () => {
    try {
      setIntegrations(await integrationsApi.list())
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Could not load integrations.')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // The OAuth callback redirects back here with the outcome. Read it once,
  // then strip the params so a refresh doesn't replay a stale banner.
  useEffect(() => {
    const status = searchParams.get('status')
    if (!status) return
    const provider = searchParams.get('provider') ?? 'The integration'
    const label = provider.charAt(0).toUpperCase() + provider.slice(1)

    setBanner(
      status === 'connected'
        ? { kind: 'success', text: `${label} connected.` }
        : {
            kind: 'error',
            text: `Could not connect ${label}. ${
              FAILURE_REASONS[searchParams.get('reason') ?? ''] ?? 'Please try again.'
            }`,
          },
    )
    setSearchParams({}, { replace: true })
  }, [searchParams, setSearchParams])

  return (
    <div className="flex min-h-screen flex-col bg-ink text-fg">
      <AppHeader />

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
        <div className="mb-6">
          <h1 className="text-xl font-medium">Integrations</h1>
          <p className="mt-1 text-sm text-muted">
            Connect the accounts you already use. Once connected, the assistant
            can act on them for you — always asking before it does anything that
            leaves your account.
          </p>
        </div>

        {banner && (
          <div
            role="status"
            className={`mb-5 flex items-start gap-3 rounded-lg border px-3 py-2.5 text-xs ${
              banner.kind === 'success'
                ? 'border-accent/30 bg-accent/10 text-accent'
                : 'border-red-500/30 bg-red-500/10 text-red-300'
            }`}
          >
            <span className="flex-1">{banner.text}</span>
            <button
              onClick={() => setBanner(null)}
              aria-label="Dismiss"
              className="shrink-0 opacity-60 transition-opacity hover:opacity-100"
            >
              ✕
            </button>
          </div>
        )}

        {loadError && (
          <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {loadError}
          </p>
        )}

        {integrations === null && !loadError && (
          <p className="font-mono text-xs tracking-wider text-muted uppercase">
            Loading…
          </p>
        )}

        {integrations?.length === 0 && (
          <p className="rounded-2xl border border-dashed border-line-bright bg-surface/40 px-6 py-12 text-center text-sm text-muted">
            No integrations are available yet.
          </p>
        )}

        <div className="flex flex-col gap-3">
          {integrations?.map((integration) => (
            <IntegrationCard
              key={integration.id}
              integration={integration}
              onChanged={load}
              onError={(text) => setBanner({ kind: 'error', text })}
            />
          ))}
        </div>
      </main>
    </div>
  )
}

function IntegrationCard({
  integration,
  onChanged,
  onError,
}: {
  integration: Integration
  onChanged: () => Promise<void>
  onError: (text: string) => void
}) {
  const [busy, setBusy] = useState(false)
  // Disconnecting revokes access upstream, so it takes a second click rather
  // than firing straight off a single press.
  const [confirming, setConfirming] = useState(false)
  const needsDrive = integration.id === 'google' && integration.connected &&
    !integration.scopes.includes('https://www.googleapis.com/auth/drive')

  async function connect() {
    setBusy(true)
    try {
      // Navigates away on success, so `busy` never needs resetting.
      await integrationsApi.connect(integration.id)
    } catch (err) {
      onError(err instanceof Error ? err.message : `Could not connect ${integration.name}.`)
      setBusy(false)
    }
  }

  async function disconnect() {
    setBusy(true)
    try {
      await integrationsApi.disconnect(integration.id)
      await onChanged()
    } catch (err) {
      onError(
        err instanceof Error ? err.message : `Could not disconnect ${integration.name}.`,
      )
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  return (
    <section className="rounded-2xl border border-line bg-surface/50 p-5">
      <div className="flex items-start gap-4">
        <ProviderIcon id={integration.id} name={integration.name} />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-medium text-fg">{integration.name}</h2>
            <span className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] tracking-wider text-muted uppercase">
              {integration.category}
            </span>
            {integration.connected && (
              <span className="flex items-center gap-1.5 font-mono text-[10px] tracking-wider text-accent uppercase">
                <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_8px_var(--color-accent)]" />
                Connected
              </span>
            )}
          </div>

          <p className="mt-1.5 text-sm text-muted">{integration.description}</p>

          {integration.capabilities.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1">
              {integration.capabilities.map((capability) => (
                <li key={capability} className="flex items-start gap-2 text-xs text-muted">
                  <span className="mt-[5px] h-1 w-1 shrink-0 rounded-full bg-line-bright" />
                  {capability}
                </li>
              ))}
            </ul>
          )}

          {integration.connected && integration.account_label && (
            <p className="mt-3 font-mono text-[11px] text-muted">
              {integration.account_label}
            </p>
          )}
          {integration.id === 'google' && (
            <p className="mt-3 text-xs text-muted">
              {needsDrive
                ? 'Your current connection needs Drive permission to create documents and PDFs. Reconnect to add access.'
                : 'Sign in to your dedicated Google account once. The assistant stays connected automatically until access expires or is revoked.'}
            </p>
          )}

          {!integration.configured && (
            <p className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-300">
              Unavailable — this server has no {integration.name} credentials
              configured.
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2">
          {needsDrive && (
            <Button variant="primary" onClick={() => void connect()} disabled={busy || !integration.configured}>
              {busy ? 'Redirecting…' : 'Enable Drive'}
            </Button>
          )}
          {integration.connected ? (
            confirming ? (
              <>
                <Button variant="outline" onClick={() => void disconnect()} disabled={busy}>
                  {busy ? 'Disconnecting…' : 'Confirm'}
                </Button>
                <button
                  onClick={() => setConfirming(false)}
                  disabled={busy}
                  className="font-mono text-[10px] tracking-wider text-muted uppercase transition-colors hover:text-fg"
                >
                  Cancel
                </button>
              </>
            ) : (
              <Button variant="ghost" onClick={() => setConfirming(true)} disabled={busy}>
                Disconnect
              </Button>
            )
          ) : (
            <Button
              variant="primary"
              onClick={() => void connect()}
              disabled={busy || !integration.configured}
            >
              {busy ? 'Redirecting…' : 'Connect'}
            </Button>
          )}
        </div>
      </div>
    </section>
  )
}
