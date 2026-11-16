// Opening a trace from anywhere on the System page: a conversation turn, or one decision, followed through
// every part it touched. The page provides it; entries and logs call it.
import { createContext, useContext } from 'react'

export type TraceTarget = { turn: string } | { attempt: string }

export const TraceContext = createContext<(target: TraceTarget) => void>(() => {})

export const useTrace = () => useContext(TraceContext)
