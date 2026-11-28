import { api } from './client'
import type { PlaceEntry, PlaceLayout, PracticeNote, PracticeNoteStatus } from '../types/practice'

/** Her practice notes (owner only): read them, write your own, fix or remove hers. */
export const practiceApi = {
  notes: () => api.get<PracticeNote[]>('/practice/notes'),
  create: (place: string, text: string) => api.post<PracticeNote>('/practice/notes', { place, text }),
  update: (id: string, change: { text?: string; status?: PracticeNoteStatus; pinned?: boolean }) =>
    api.patch<PracticeNote>(`/practice/notes/${id}`, change),
  remove: (id: string) => api.delete<void>(`/practice/notes/${id}`),
}

/** What she knows of each place that stays the same (owner only): read it, fix or remove one thing, fix her own
 *  lines, forget a place. */
export const placesApi = {
  list: () => api.get<PlaceLayout[]>('/practice/places'),
  update: (id: string, change: { her_layout?: string }) => api.patch<PlaceLayout>(`/practice/places/${id}`, change),
  remove: (id: string) => api.delete<void>(`/practice/places/${id}`),
  updateEntry: (id: string, text: string) => api.patch<PlaceEntry>(`/practice/places/entries/${id}`, { text }),
  removeEntry: (id: string) => api.delete<void>(`/practice/places/entries/${id}`),
}
