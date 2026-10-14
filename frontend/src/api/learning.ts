import { api } from './client'
import type { Lesson, LessonStatus, Proposal, ProposalStatus, Reflection } from '../types/learning'

/** What she learned from playing: lessons, code proposals, reflections. */
export const learningApi = {
  lessons: () => api.get<Lesson[]>('/learning/lessons'),
  setLessonStatus: (id: string, status: Exclude<LessonStatus, 'confirmed'>) =>
    api.patch<Lesson>(`/learning/lessons/${id}`, { status }),
  deleteLesson: (id: string) => api.delete<void>(`/learning/lessons/${id}`),

  proposals: () => api.get<Proposal[]>('/learning/proposals'),
  updateProposal: (id: string, change: { status?: ProposalStatus; user_note?: string }) =>
    api.patch<Proposal>(`/learning/proposals/${id}`, change),

  reflections: () => api.get<Reflection[]>('/learning/reflections'),
}
