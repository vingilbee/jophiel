import { api } from './client'
import type {
  AdaptationLessonDetail,
  AdaptationLessonStatus,
  AdaptationListing,
  AssessmentDetail,
  AssessmentListing,
  Challenge,
  LearnedSkill,
  Lesson,
  LessonStatus,
  Proposal,
  ProposalStatus,
  Reflection,
} from '../types/learning'

/** What she learned from playing: moves she taught herself, lessons, code proposals, reflections. */
export const learningApi = {
  lessons: () => api.get<Lesson[]>('/learning/lessons'),
  setLessonStatus: (id: string, status: Exclude<LessonStatus, 'confirmed'>) =>
    api.patch<Lesson>(`/learning/lessons/${id}`, { status }),
  deleteLesson: (id: string) => api.delete<void>(`/learning/lessons/${id}`),

  proposals: () => api.get<Proposal[]>('/learning/proposals'),
  updateProposal: (id: string, change: { status?: ProposalStatus; user_note?: string }) =>
    api.patch<Proposal>(`/learning/proposals/${id}`, change),

  reflections: () => api.get<Reflection[]>('/learning/reflections'),

  challenges: () => api.get<Challenge[]>('/learning/challenges'),
  skills: () => api.get<LearnedSkill[]>('/learning/skills'),
  setSkillEnabled: (id: string, enabled: boolean) => api.patch<LearnedSkill>(`/learning/skills/${id}`, { enabled }),
  deleteSkill: (id: string) => api.delete<void>(`/learning/skills/${id}`),

  /** What feedback and outcomes said about what she did (retrospective assessment). */
  assessments: (outcome?: string) =>
    api.get<AssessmentListing>(`/learning/assessments${outcome ? `?outcome=${encodeURIComponent(outcome)}` : ''}`),
  assessment: (id: string) => api.get<AssessmentDetail>(`/learning/assessments/${id}`),
  noteAssessment: (id: string, text: string) => api.post<AssessmentDetail>(`/learning/assessments/${id}/notes`, { text }),

  /** Lessons she keeps from supported assessments, and which decisions had them (read only). */
  adaptationLessons: (status?: AdaptationLessonStatus) =>
    api.get<AdaptationListing>(`/learning/adaptation/lessons${status ? `?status=${status}` : ''}`),
  adaptationLesson: (id: string) => api.get<AdaptationLessonDetail>(`/learning/adaptation/lessons/${id}`),
}
