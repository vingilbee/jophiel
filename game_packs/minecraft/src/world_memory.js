// What she remembers about one world: ground she has explored (chunks) and places she noted.
// One instance per world, shared by explore and the noticing layer, saved to var/explore/.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

const SAVE_DELAY_MS = 2000

export class WorldMemory {
  constructor (path) {
    this.path = path
    this.visited = new Set()
    this.places = []
    this._timer = null
    if (!path) return
    try {
      const saved = JSON.parse(readFileSync(path, 'utf8'))
      this.visited = new Set(saved.visited ?? [])
      this.places = saved.places ?? []
    } catch {}
  }

  // Note a place unless one of the same kind is already noted within `spacing` blocks.
  // Returns the new place, or null if it was already known.
  note (kind, position, { spacing = 48, name = null } = {}) {
    const near = this.places.some((p) => p.kind === kind && (!name || p.name === name) &&
      Math.hypot(p.x - position.x, p.z - position.z) < spacing)
    if (near) return null
    const place = {
      kind,
      ...(name ? { name } : {}),
      x: Math.round(position.x),
      y: Math.round(position.y),
      z: Math.round(position.z),
      noted_at: new Date().toISOString()
    }
    this.places.push(place)
    this.saveSoon()
    return place
  }

  placesSince (iso) {
    return this.places.filter((p) => p.noted_at >= iso)
  }

  saveSoon () {
    if (!this.path || this._timer) return
    this._timer = setTimeout(() => {
      this._timer = null
      this.save()
    }, SAVE_DELAY_MS)
    this._timer.unref?.()
  }

  save () {
    if (!this.path) return
    mkdirSync(dirname(this.path), { recursive: true })
    writeFileSync(this.path, JSON.stringify({ visited: [...this.visited], places: this.places }))
  }
}
