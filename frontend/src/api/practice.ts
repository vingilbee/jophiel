import { api } from './client'
import type { PracticeNote, PracticeNoteStatus } from '../types/practice'

/** Her practice notes (owner only): read them, write your own, fix or remove hers. */
export const practiceApi = {
  notes: () => api.get<PracticeNote[]>('/practice/notes'),
  create: (place: string, text: string) => api.post<PracticeNote>('/practice/notes', { place, text }),
  update: (id: string, change: { text?: string; status?: PracticeNoteStatus }) =>
    api.patch<PracticeNote>(`/practice/notes/${id}`, change),
  remove: (id: string) => api.delete<void>(`/practice/notes/${id}`),
}
