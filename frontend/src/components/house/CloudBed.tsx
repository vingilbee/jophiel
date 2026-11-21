import sleeping from '../../assets/jophiel/sleeping.webp'

const BASE = [
  [92, 214, 66],
  [168, 184, 82],
  [270, 170, 92],
  [378, 174, 90],
  [472, 196, 78],
  [540, 228, 56],
  [70, 262, 52],
  [150, 270, 64],
  [262, 276, 70],
  [380, 276, 70],
  [486, 268, 62],
  [552, 270, 42],
] as const

const PILLOW = [
  [132, 132, 34],
  [172, 120, 40],
  [214, 134, 32],
] as const

const STARS = [
  [330, 150],
  [420, 140],
  [500, 158],
  [372, 196],
  [462, 206],
  [530, 196],
  [300, 210],
] as const

function star(x: number, y: number, r: number) {
  const i = r * 0.32
  return `M${x} ${y - r}L${x + i} ${y - i}L${x + r} ${y}L${x + i} ${y + i}L${x} ${y + r}L${x - i} ${y + i}L${x - r} ${y}L${x - i} ${y - i}Z`
}

const BLANKET =
  'M244 140C300 122 420 120 538 134C566 138 576 160 568 186C562 210 558 230 550 244Q522 258 494 246Q464 260 434 248Q402 262 372 249Q340 262 308 248Q278 260 254 244C246 208 240 170 244 140Z'
const FOLD = 'M244 140C258 135 274 132 290 132C286 170 288 210 294 246Q274 256 254 244C246 208 240 170 244 140Z'

export function CloudBed() {
  return (
    <div className="house-bed" role="img" aria-label="Jophiel sleeping on her cloud bed">
      <svg viewBox="0 0 620 340">
        <defs>
          <linearGradient id="bed-cloud" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#FFF7F0" />
            <stop offset="0.55" stopColor="#F3E8F8" />
            <stop offset="1" stopColor="#CDB8EA" />
          </linearGradient>
          <linearGradient id="bed-blanket" x1="0" y1="0" x2="0.3" y2="1">
            <stop offset="0" stopColor="#D9C6F4" />
            <stop offset="1" stopColor="#A98BD8" />
          </linearGradient>
        </defs>
        <g transform="translate(620 0) scale(-1 1)">
          <g stroke="#6F5596" strokeWidth="7" fill="#6F5596">
            {BASE.map(([x, y, r], i) => (
              <circle key={i} cx={x} cy={y} r={r} />
            ))}
          </g>
          <g fill="url(#bed-cloud)">
            {BASE.map(([x, y, r], i) => (
              <circle key={i} cx={x} cy={y} r={r} />
            ))}
          </g>
        </g>
        <g transform="translate(620 86) scale(-1 1)">
          <g stroke="#6F5596" strokeWidth="6" fill="#6F5596">
            {PILLOW.map(([x, y, r], i) => (
              <circle key={i} cx={x} cy={y} r={r} />
            ))}
          </g>
          <g fill="#FFFBF6">
            {PILLOW.map(([x, y, r], i) => (
              <circle key={i} cx={x} cy={y} r={r} />
            ))}
          </g>
          <ellipse cx="172" cy="128" rx="30" ry="11" fill="#D8C8EE" opacity="0.55" />
        </g>
        <image className="house-sleeper" href={sleeping} x="69" y="-43" width="465" height="268" preserveAspectRatio="xMidYMax meet" />
        <g transform="translate(620 -59) scale(-1 1.58)">
          <path d={BLANKET} fill="url(#bed-blanket)" stroke="#6F5596" strokeWidth="4" strokeLinejoin="round" />
          <path d="M330 142C334 180 336 216 340 252M430 138C436 176 438 214 440 254" fill="none" stroke="#9479C4" strokeWidth="3" strokeLinecap="round" opacity="0.55" />
          {STARS.map(([x, y], i) => (
            <path key={i} d={star(x, y, 7)} fill="#F8D98E" stroke="#B8893F" strokeWidth="1.2" strokeLinejoin="round" />
          ))}
          <path d={FOLD} fill="#F6EEFC" stroke="#6F5596" strokeWidth="4" strokeLinejoin="round" />
        </g>
      </svg>
    </div>
  )
}
