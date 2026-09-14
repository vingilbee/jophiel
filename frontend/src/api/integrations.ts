import { api } from './client'
import type { Integration } from '../types'

interface AuthorizeResponse {
  authorization_url: string
}

export const integrationsApi = {
  /** Every provider the server offers, with this user's connection status. */
  list: () => api.get<Integration[]>('/integrations'),

  get: (providerId: string) => api.get<Integration>(`/integrations/${providerId}`),

  /**
   * Start a provider's OAuth flow.
   *
   * The consent URL comes back as JSON rather than a redirect so this `fetch`
   * can read it; we then navigate the whole window there. It has to be a
   * top-level navigation (not a popup or XHR) because the provider redirects
   * back to our callback, which needs the session cookie to identify the user.
   */
  connect: async (providerId: string): Promise<void> => {
    const { authorization_url } = await api.get<AuthorizeResponse>(
      `/integrations/${providerId}/authorize`,
    )
    window.location.assign(authorization_url)
  },

  disconnect: (providerId: string) => api.delete<void>(`/integrations/${providerId}`),
}
