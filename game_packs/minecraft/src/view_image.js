// Two pictures of one view (src/view.js), from the same rays, for comparing what her decisions get as
// text, as images, or both (docs/MINECRAFT_RAY_BASED_PERCEPTION_DESIGN.md, "Aligned material and depth
// images"). Not sent to any model by itself: GET /view/image serves them.
//
//   material: a fixed colour per kind of surface (LEGEND.material), shaded by the face it hit (tops
//             brightest), with glass she sees through tinted;
//   depth:    distance along the ray on a fixed square-root scale, white at her eyes to dark grey at the
//             view's range, never rescaled between frames; rays that hit nothing within range, that rose past
//             the top of the world, or that reached unloaded terrain each have their own colour.
// Landmarks in view are boxed and labelled (L3) on both, at the same place.
import { deflateSync, crc32 } from 'node:zlib'

import { END, NORMALS, project } from './view.js'
import { built } from './scene.js'

export const LEGEND = {
  material: {
    stone: [125, 125, 125], dirt: [134, 96, 67], grass: [96, 150, 62], sand: [219, 207, 142], gravel: [140, 132, 128],
    wood: [116, 86, 50], leaves: [52, 112, 44], water: [48, 92, 206], lava: [232, 104, 24], ore: [214, 178, 62],
    built: [204, 66, 66], snow_ice: [236, 242, 250], other: [168, 160, 150], dark: [18, 18, 18],
    clear: [190, 206, 222], sky: [138, 188, 236], unknown: [120, 40, 140], murk: [16, 70, 74], skipped: [0, 0, 0]
  },
  depth: {
    scale: 'surface: grey = 245 - 205 × sqrt(distance / range): 245 at 0 m, 143 at 32 m, 40 at the range (128 m); ' +
      'the same every view (square root: more contrast up close, where she can act)',
    clear: [10, 16, 70], sky: [20, 70, 130], unknown: [120, 40, 140], murk: [16, 70, 74], skipped: [0, 0, 0]
  },
  shade: 'material only: top faces ×1.0, sides ×0.8, bottoms ×0.6; glass passed through: tinted pale cyan'
}

function materialOf (name) {
  if (!name) return 'other'
  if (name === 'water') return 'water'
  if (name === 'lava') return 'lava'
  if (name.endsWith('_ore') || name === 'ancient_debris') return 'ore'
  if (/(_leaves)$/.test(name)) return 'leaves'
  if (/(_log|_wood|_stem|_hyphae)$/.test(name) && !name.startsWith('stripped_')) return 'wood'
  if (built(name)) return 'built'
  if (/^(grass_block|moss_block|mycelium|podzol)$/.test(name)) return 'grass'
  if (/dirt|mud|clay|farmland|rooted/.test(name)) return 'dirt'
  if (/sand(stone)?$|^sand$|red_sand/.test(name)) return 'sand'
  if (name === 'gravel') return 'gravel'
  if (/snow|ice/.test(name)) return 'snow_ice'
  if (/stone|deepslate|andesite|diorite|granite|tuff|calcite|basalt|blackstone|netherrack|bedrock|obsidian|terracotta/.test(name)) return 'stone'
  return 'other'
}

const ENDS = { [END.CLEAR]: 'clear', [END.SKY]: 'sky', [END.UNKNOWN]: 'unknown', [END.MURK]: 'murk', [END.SKIPPED]: 'skipped' }

function rayColour (obs, k, layer) {
  const { store } = obs
  const end = store.end[k]
  if (end !== END.SURFACE) return LEGEND[layer][ENDS[end]]
  if (layer === 'depth') {
    const v = Math.round(245 - Math.sqrt(Math.min(store.dist[k], obs.camera.range) / obs.camera.range) * 205)
    return [v, v, v]
  }
  const name = store.name[k] === -2 ? null : obs.names[store.name[k]]
  let c = store.name[k] === -2 ? LEGEND.material.dark : LEGEND.material[materialOf(name)]
  const n = NORMALS[store.normal[k]]
  const shade = !n ? 1 : n[1] === 1 ? 1 : n[1] === -1 ? 0.6 : 0.8
  c = c.map((v) => Math.round(v * shade))
  if (!Number.isNaN(store.glassAt[k])) c = c.map((v, i) => Math.round(v * 0.6 + [200, 240, 245][i] * 0.4))
  return c
}

// A tiny 3 × 5 font: what labels need.
const GLYPHS = {
  L: ['100', '100', '100', '100', '111'], 0: ['111', '101', '101', '101', '111'], 1: ['010', '110', '010', '010', '111'],
  2: ['111', '001', '111', '100', '111'], 3: ['111', '001', '111', '001', '111'], 4: ['101', '101', '111', '001', '001'],
  5: ['111', '100', '111', '001', '111'], 6: ['111', '100', '111', '101', '111'], 7: ['111', '001', '010', '010', '010'],
  8: ['111', '101', '111', '101', '111'], 9: ['111', '101', '111', '001', '111']
}

class Canvas {
  constructor (w, h) {
    this.w = w
    this.h = h
    this.px = new Uint8Array(w * h * 3)
  }

  set (x, y, c) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return
    const i = (y * this.w + x) * 3
    this.px[i] = c[0]; this.px[i + 1] = c[1]; this.px[i + 2] = c[2]
  }

  rect (x0, y0, x1, y1, c) {
    for (let x = x0; x <= x1; x++) { this.set(x, y0, c); this.set(x, y1, c) }
    for (let y = y0; y <= y1; y++) { this.set(x0, y, c); this.set(x1, y, c) }
  }

  text (x, y, str, size, c, bg) {
    const width = str.length * 4 * size
    for (let yy = y - 1; yy < y + 5 * size + 1; yy++) for (let xx = x - 1; xx < x + width; xx++) this.set(xx, yy, bg)
    let cx = x
    for (const ch of str) {
      const g = GLYPHS[ch]
      if (g) {
        for (let r = 0; r < 5; r++) {
          for (let col = 0; col < 3; col++) {
            if (g[r][col] !== '1') continue
            for (let dy = 0; dy < size; dy++) for (let dx = 0; dx < size; dx++) this.set(cx + col * size + dx, y + r * size + dy, c)
          }
        }
      }
      cx += 4 * size
    }
  }

  png () {
    const raw = Buffer.alloc((this.w * 3 + 1) * this.h)
    for (let y = 0; y < this.h; y++) {
      raw[y * (this.w * 3 + 1)] = 0
      Buffer.from(this.px.buffer, y * this.w * 3, this.w * 3).copy(raw, y * (this.w * 3 + 1) + 1)
    }
    const chunk = (type, data) => {
      const len = Buffer.alloc(4)
      len.writeUInt32BE(data.length)
      const td = Buffer.concat([Buffer.from(type, 'ascii'), data])
      const crc = Buffer.alloc(4)
      crc.writeUInt32BE(crc32(td) >>> 0)
      return Buffer.concat([len, td, crc])
    }
    const ihdr = Buffer.alloc(13)
    ihdr.writeUInt32BE(this.w, 0)
    ihdr.writeUInt32BE(this.h, 4)
    ihdr[8] = 8 // bit depth
    ihdr[9] = 2 // RGB
    return Buffer.concat([
      Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
      chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))
    ])
  }
}

// layer: 'material' | 'depth'. labels: [{ id, x, y, z }] (landmarks in view). scale: pixels per ray.
export function renderView (obs, { layer = 'material', labels = [], scale = 8 } = {}) {
  const { cols, rows } = obs.camera
  const c = new Canvas(cols * scale, rows * scale)
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const colour = rayColour(obs, j * cols + i, layer)
      for (let y = 0; y < scale; y++) for (let x = 0; x < scale; x++) c.set(i * scale + x, j * scale + y, colour)
    }
  }
  const size = Math.max(1, Math.round(scale / 4))
  for (const l of labels) {
    const p = project(obs.camera, { x: l.x + 0.5, y: l.y + 0.5, z: l.z + 0.5 })
    if (!p || p.i < 0 || p.j < 0 || p.i >= cols || p.j >= rows) continue
    const x = Math.round(p.i * scale)
    const y = Math.round(p.j * scale)
    c.rect(x - 2 * size, y - 2 * size, x + 2 * size, y + 2 * size, [255, 255, 0])
    c.text(x + 3 * size, y - 6 * size, l.id, size, [255, 255, 0], [0, 0, 0])
  }
  return c.png()
}
