// What it takes to make an item, against what she carries, and close names for a wrong one.
// Columbina asks before planning ("what does a bed need?"), so she can ask the user for exactly
// what's missing instead of finding out by failing.

const MAX_RECIPES = 3
const MAX_SUGGESTIONS = 6

// Names close to `name` from `names`: ones with it as a whole word ("bed" -> white_bed, the
// plain white/oak variant first), then ones containing it (bedrock), then ones sharing a word
// with it ("oak plank" -> oak_planks).
export function suggestNames (names, name) {
  const wanted = String(name ?? '').toLowerCase().trim().replace(/[\s-]+/g, '_')
  if (!wanted) return []
  const words = wanted.split('_').filter((w) => w.length >= 3)
  const others = names.filter((n) => n !== wanted)
  const whole = others.filter((n) => `_${n}_`.includes(`_${wanted}_`))
  const containing = others.filter((n) => !whole.includes(n) && n.includes(wanted))
  const sharing = others.filter((n) => !whole.includes(n) && !containing.includes(n) &&
    words.some((w) => n.split('_').some((part) => part === w || part === `${w}s` || `${part}s` === w)))
  const plain = (n) => (/^(white|oak)_/.test(n) ? 0 : 1)
  const byLength = (a, b) => plain(a) - plain(b) || a.length - b.length || a.localeCompare(b)
  return [...whole.sort(byLength), ...containing.sort(byLength), ...sharing.sort(byLength)].slice(0, MAX_SUGGESTIONS)
}

// "did you mean" text for a ParamError, or ''.
export function didYouMean (names, name) {
  const close = suggestNames(names, name)
  return close.length ? ` (did you mean: ${close.join(', ')}?)` : ''
}

export function carried (bot) {
  const have = {}
  for (const item of bot.inventory.items()) have[item.name] = (have[item.name] ?? 0) + item.count
  return have
}

function describe (bot, recipe, have) {
  const needs = {}
  for (const { id, count } of recipe.delta ?? []) {
    if (count < 0) {
      const name = bot.registry.items[id]?.name ?? String(id)
      needs[name] = (needs[name] ?? 0) - count
    }
  }
  const missing = {}
  for (const [name, n] of Object.entries(needs)) {
    if ((have[name] ?? 0) < n) missing[name] = n - (have[name] ?? 0)
  }
  return { needs, makes: recipe.result?.count ?? 1, needs_crafting_table: Boolean(recipe.requiresTable), missing }
}

// { item, known, ... }: the recipe variants closest to what she has (fewest missing first).
export function recipeFor (bot, name) {
  const item = bot.registry.itemsByName[name]
  if (!item) {
    return { item: name, known: false, did_you_mean: suggestNames(Object.keys(bot.registry.itemsByName), name) }
  }
  const have = carried(bot)
  const tableNearby = Boolean(bot.findBlock({ matching: bot.registry.blocksByName.crafting_table.id, maxDistance: 32 }))
  const all = bot.recipesAll(item.id, null, true).map((r) => describe(bot, r, have))
  const seen = new Set()
  const recipes = all
    .sort((a, b) => Object.keys(a.missing).length - Object.keys(b.missing).length ||
      Object.values(a.missing).reduce((s, n) => s + n, 0) - Object.values(b.missing).reduce((s, n) => s + n, 0))
    .filter((r) => {
      const key = JSON.stringify(r.needs)
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  return {
    item: name,
    known: true,
    craftable: recipes.length > 0,
    have: have[name] ?? 0,
    crafting_table_nearby: tableNearby,
    crafting_table_carried: (have.crafting_table ?? 0) > 0,
    variants: recipes.length,
    recipes: recipes.slice(0, MAX_RECIPES)
  }
}
