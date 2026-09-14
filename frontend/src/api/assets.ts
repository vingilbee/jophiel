import type { StoredAsset } from '../types'

export async function uploadAsset(file: File): Promise<StoredAsset> {
  const body = new FormData()
  body.append('file', file, file.name)
  const res = await fetch('/api/assets', {
    method: 'POST', body, credentials: 'include',
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? 'Asset upload failed.')
  }
  return res.json()
}

export const assetUrl = (assetId: string) => `/api/assets/${assetId}/raw`

export async function getAsset(assetId: string): Promise<StoredAsset> {
  const response = await fetch(`/api/assets/${assetId}`, { credentials: 'include' })
  if (!response.ok) throw new Error('This file is unavailable or belongs to another account.')
  return response.json()
}
