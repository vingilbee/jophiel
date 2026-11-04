// How a part's state shows, shared by the map and its own page.
import type { PartState } from '../../types/system'

export const STATE_WORD: Record<PartState, string> = {
  alive: 'alive',
  busy: 'working',
  partial: 'partly down',
  down: 'down',
  failing: 'failing',
  off: 'off',
}

/** How often a core charges and discharges in each state, in seconds; none when it isn't running. */
export const PERIOD: Partial<Record<PartState, number>> = { alive: 2.8, busy: 1.05, partial: 4.4, failing: 1.6 }

export const FAIL = '#FF4136'
