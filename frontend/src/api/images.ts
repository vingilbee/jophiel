// Image store client. Photos are uploaded once and then referred to by
// `image_id` — the chat socket sends ids, not pixels, and the agent passes
// those ids to the food-vision specialist.
import type { StoredImage } from '../types'

/**
 * Upload a photo and get back its stored record.
 *
 * Uses FormData directly rather than the JSON `api` wrapper (same reason as
 * predict.ts): the browser must set its own multipart boundary.
 *
 * `kind` separates the two very different things this endpoint stores. An
 * "upload" is a photo the user is keeping; a "screen" frame is a moment of a
 * live sense, pruned aggressively server-side and kept out of the photo
 * listing so an hour of sharing never becomes an hour of scrollback.
 */
export async function uploadImage(
  file: Blob,
  kind: 'upload' | 'screen' = 'upload',
): Promise<StoredImage> {
  const body = new FormData()
  // A Blob has no name; multipart needs one, and the server uses it only for
  // the extension.
  body.append('file', file, file instanceof File ? file.name : 'screen.jpg')

  const res = await fetch(`/api/images?kind=${kind}`, {
    method: 'POST',
    body,
    credentials: 'include',
  })

  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? 'Upload failed.')
  }
  return res.json()
}

/** URL serving the image itself — safe to use straight as an <img src>. */
export const imageUrl = (imageId: string) => `/api/images/${imageId}/raw`

export const imagesApi = {
  upload: uploadImage,
  url: imageUrl,
}
