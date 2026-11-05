// Motion styles (plan doc stage G, motion style layer). A skill picks one; the numbers live here.
// Angles in degrees, times in ms.
export const STYLES = {
  // Exploring, walking somewhere, following: unhurried.
  relaxed: {
    maxTurn: 260, // top turning speed, deg/s
    accel: 1400, // how fast turning speeds up and slows down, deg/s²
    gain: 7, // how hard she closes the remaining angle, 1/s (higher = snappier ease-out)
    reaction: [200, 300], // delay before reacting to something new
    wobble: 1.2, // slow aim drift amplitude
    walkPitch: 8, // looks this far down at the ground ahead while walking
    // Steering along a path is quicker than looking at things: the pathfinder needs her within
    // 0.35 blocks of each corner, and a slow turn overshoots it and loops back.
    walkTurn: { maxTurn: 420, accel: 5000, gain: 14 },
    sprint: 'mixed', // sprints on long straight stretches, in moods
    glances: true, // glances at mobs and players, looks around when idle
    missChance: 0
  },
  // Mining, crafting, eating, sleeping: steady hands.
  focused: {
    maxTurn: 320,
    accel: 1800,
    gain: 9,
    reaction: [150, 250],
    wobble: 0.6,
    walkPitch: 12,
    walkTurn: { maxTurn: 450, accel: 5500, gain: 15 },
    sprint: 'rarely', // only on long trips
    glances: false,
    missChance: 0
  },
  // Fighting: fast but never perfect.
  combat: {
    maxTurn: 540,
    accel: 3600,
    gain: 12,
    reaction: [150, 220],
    wobble: 1.0,
    walkPitch: 4,
    walkTurn: { maxTurn: 600, accel: 7000, gain: 18 },
    sprint: 'always', // whatever the pathfinder wants
    glances: false,
    missChance: 0.08 // a swing that goes wide now and then
  }
}

// Which style each skill moves in.
export const SKILL_STYLE = {
  go_to: 'relaxed',
  follow_player: 'relaxed',
  mine: 'focused',
  craft: 'focused',
  eat: 'focused',
  sleep: 'focused',
  fight: 'combat',
  place: 'focused',
  collect_items: 'relaxed',
  give: 'relaxed',
  look_at: 'relaxed',
  store: 'focused',
  take: 'focused',
  smelt: 'focused'
}
