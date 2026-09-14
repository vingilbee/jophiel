import { api, ApiError } from './client'
import type { Prediction, PredictionMode } from '../types'

/**
 * Upload a food image and get predicted macros.
 *
 * Uses `api.raw` (not `api.post`) because this is multipart/form-data — we must
 * let the browser set the Content-Type boundary, so we can't go through the
 * JSON wrapper that forces `application/json`.
 */
export async function predictMacros(
  file: File,
  mode: PredictionMode = 'combined',
): Promise<Prediction> {
  const form = new FormData()
  form.append('file', file)

  const res = await api.raw(`/predict?mode=${mode}`, {
    method: 'POST',
    body: form,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body — keep statusText */
    }
    throw new ApiError(res.status, detail)
  }

  return (await res.json()) as Prediction
}
