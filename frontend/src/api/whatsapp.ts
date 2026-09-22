import { api } from './client'

export interface WhatsAppLinkStatus {
  configured: boolean
  linked: boolean
  display_name: string | null
  wa_id_suffix: string | null
}

export interface WhatsAppLinkCode {
  code: string
  expires_at: string
  instruction: string
}

export const whatsappApi = {
  status: () => api.get<WhatsAppLinkStatus>('/whatsapp/link'),
  createLinkCode: () => api.post<WhatsAppLinkCode>('/whatsapp/link-code'),
  unlink: () => api.delete<void>('/whatsapp/link'),
}
