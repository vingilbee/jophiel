import { api } from './client'
import type {
  AdaptationLessonDetail,
  AdaptationLessonStatus,
  AdaptationListing,
  AssessmentDetail,
  AssessmentListing,
  Challenge,
  LearnedSkill,
  TrialDetail,
  TrialListing,
} from '../types/learning'

/** What she learned from playing: shared assessments and lessons, trials and moves she taught herself. */
export const learningApi = {
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

  /** Bounded game trials: what she tested, what she predicted, what her mind chose, what happened (read only). */
  trials: () => api.get<TrialListing>('/learning/trials'),
  trial: (id: string) => api.get<TrialDetail>(`/learning/trials/${id}`),
}
