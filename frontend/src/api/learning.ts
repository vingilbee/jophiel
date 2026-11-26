import { api } from './client'
import type { Challenge, LearnedSkill } from '../types/learning'

/** What she learned from playing: the challenges she set herself and the moves she taught herself. */
export const learningApi = {
  challenges: () => api.get<Challenge[]>('/learning/challenges'),
  skills: () => api.get<LearnedSkill[]>('/learning/skills'),
  setSkillEnabled: (id: string, enabled: boolean) => api.patch<LearnedSkill>(`/learning/skills/${id}`, { enabled }),
  deleteSkill: (id: string) => api.delete<void>(`/learning/skills/${id}`),
}
