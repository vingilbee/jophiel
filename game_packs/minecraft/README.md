# Columbina's Minecraft pack

Stage G of `docs/DESKTOP_AGENT_IMPLEMENTATION_PLAN.md`. A small Node.js service on the Mac that joins a
Minecraft world as her own player, reads her state, lets you watch, and runs **skills** (go_to,
mine, fight, explore, eat, sleep, craft, follow_player, place, collect_items, give, look_at, store,
take, smelt, wear, hold, put_in_hotbar, surface) one at a time when asked through its API, and keeps a list of what she **notices**.
Phases 0-3 are built: she joins, reads her state, runs skills, moves through a natural-motion
layer, notices things, and **her hands (the driver model) pick skills** in a game session that
Columbina starts and steers from the chat or voice.

For Minecraft this replaces the VM key-pressing. You play in the VM as usual and join the same world.

## Pieces

| File | Job |
|---|---|
| `src/index.js` | Joins the world (reconnects with backoff), starts the API and the viewer |
| `src/state.js` | State reader: position, facing, health, food, held item, inventory, time of day, weather, nearby entities with distance and side (incl. behind) |
| `src/describe.js` | The state as a few lines of text, for her hands (phase 3) |
| `src/api.js` | `GET /health`, `GET /state` on 127.0.0.1, bearer token, read-only |
| `src/viewer.js` | Watch-only 3D view in the browser, 127.0.0.1 only |
| `scripts/server.js` | Sets up and starts a private vanilla server on the Mac |
| `src/check.js` | Pings the world: reachable? which version? supported? |
| `src/skills/` | The skills, the skill contract (`contract.js`) and the runner with the shared safety (`runner.js`) |
| `src/motion/` | The motion style layer: smooth eased turning, reaction time, aim wobble, pace, gaze (`motion.js`), the maths (`gaze.js`), the numbers per style (`styles.js`) |
| `src/noticing.js` | The noticing layer: villages, mobs, ores, lava, night, players, damage, noticed once each |
| `src/world_memory.js` | Her memory of each world: explored ground and noted places (`var/explore/`) |
| `src/skills/doors.js` | Routes through doors/gates, opens them on the way, closes them behind her |
| `src/sandbox.js` | The sealed box her own code runs in (stage H): skills, single actions, state and log only |
| `src/actions.js` | Single actions for her code (`act.attack/dig/place/look/equip/jump`): what a move no skill has is built from |
| `src/pack.js` | Wires pathfinding + skills + runner into a bot (the service and the tests use the same wiring) |
| `scripts/smoke.js` | End-to-end check against a throwaway flying-squid server (no Minecraft needed) |
| `test/integration/` | Every skill against a real vanilla server (flat test world, set up by console commands) |

Safety: she only joins LAN or loopback addresses; the API and viewer bind to 127.0.0.1 only and
need the token; one skill runs at a time; she only fights hostile mobs.

Version: **the newest release Mineflayer supports** (26.1.2 with Mineflayer 4.39), unless
`MC_VERSION` pins one. Updating Mineflayer (`bun update mineflayer`) moves her player and the
server together: the next `start` downloads the new server and the world upgrades on first load.
It never goes back down, since an older server can't open a newer world. Minecraft 26.x needs
**Java 25**: `brew install openjdk@25` (found automatically). Your game in the VM must be on the same
version (a launcher installation for it).

## How she joins

The world runs on a server **on the Mac**, not in the VM: hosting inside the VM makes your game
lag, and the VM's NAT stops the Mac from reaching a world opened there. You join from the VM.

Use **accounts on** (`--online`): you sign in with your account, she signs in with her own
(your second account, `MC_AUTH=microsoft`), and the server checks both. Nobody can join under
your names. (Without `--online` accounts are off and a whitelist by *name* is the only lock;
only useful if she has no account.)

## Set up (once)

Needs **Node 24 or newer** (`isolated-vm`, the box her own code runs in, ships builds only for
those; Node 23 can't load it). `brew upgrade node`.

```bash
cd game_packs/minecraft
bun install
cp .env.example .env
```

In `.env`: `MC_HOST=192.168.1.76` (the Mac's LAN address, which the VM already reaches) and
`MC_AUTH=microsoft`.

Optional, to bring **your existing world**: in the VM, copy the world's folder from
`%APPDATA%\.minecraft\saves\<world>` to the Mac. It must come from 26.1.2 or older (a newer save
won't load). Skip `--world` for a fresh world.

```bash
# Your in-game name, her account's in-game name.
node scripts/server.js setup --bind 192.168.1.76 --online --player <your-name> --bot <her-name> --op [--world <copied-folder>]
# Read https://aka.ms/MinecraftEULA, then the same command plus --accept-eula.
```

`--op` makes you an operator so you can spectate her (below); leave it off if you don't want that.

## Run

In mprocs, start `minecraft-server`, wait for `Done`, then start `minecraft-pack`. Or by hand:

```bash
node scripts/server.js start             # terminal 1
bun run check                            # can the Mac reach it, which version is it
bun run start                            # terminal 2
bun run state                            # anytime: what she knows right now (--json for the raw state)
```

**First start only:** the pack prints `sign in for her account: open https://microsoft.com/link and
enter ABCD1234`. Sign in there with **her** (second) account. The token is kept in `var/auth/`, so
you won't be asked again.

## Watch her: three ways

1. **Join the same world.** In the VM, Minecraft Launcher → Installations → New → the version
   `bun run check` reports (26.1.2 today).
   Multiplayer → Add Server → `192.168.1.76`. She's the player with her account's name.
2. **Browser view:** http://127.0.0.1:8791 on the Mac (third person; set
   `PACK_VIEWER_FIRST_PERSON=true` for her eyes). Reload the page after she reconnects.
   **Only up to 1.21.4**: prismarine-viewer has no graphics for 26.x, so the pack skips it and says so.
3. **Through her eyes in your client** (needs `--op`): `/gamemode spectator`, then `/spectate <her-name>`.
   `/gamemode survival` to come back.

## Skills

Every skill follows the same contract: one goal, one fast loop, no AI inside, never calls another
skill (her hands chain them). It always ends with exactly one outcome: `done`, `failed` (with why),
`lost`, `blocked`, `hurt`, `timeout` or `stopped`, plus a few numbers.

No skill sees through walls: `mine`, gather, `go_to block:`, `explore`'s goal and her notable blocks
count only blocks with a face in her line of sight, or that she has seen before (`src/sight.js`).

**The shape of the place** (`src/surroundings.js`, in the state as `surroundings` and in the text as
"Around you / Ways / Openings"): what's over her head (open sky, trees, a roof, or underground); for
each of the 8 compass directions how far she can walk along the ground (one block up, at most three
down, like walking) and what stops her there (a wall and how high, a drop and how deep, water, lava),
with the spot each walk ends so `go_to` can take her there; and, from outdoors, cave or tunnel mouths:
walkable ground that goes in under rock for 3+ blocks, seen along 16 directions. A walk reads the
ground in front of her and stops at the first wall, so it doesn't see through hills. Other players'
lines say which way they face ("facing east (your right)"), so "this way" from the user means
something. Her air is read only from her own player (`src/air.js`): mineflayer 4.39 took any
entity's air as hers, so a fish on land nearby had her crying out for air on dry ground.

**The lie of the land** (`src/terrain.js`, in the state as `terrain` and in the text as "Land / Highest
ground (peak) / Surface / Way out"): the ground height every 4 blocks out to 64 m (trees aren't
ground), so she knows which way the land rises or falls, where the water is, where the highest ground
is and how steep the straight way up gets (the biggest step from one block to the next). Underground:
how far the surface is straight up, and the way out, found by searching open space (air and water,
ledges and shafts too) for the nearest spot with sky above, then judged: walkable, or the biggest
climb on it (height gained between two spots she can stand). Each column's ground is scanned once and
forgotten when a block in it changes; the whole thing is recomputed after she moves 2 blocks or every
5 s. `go_to` targets `peak` and `surface` go there (`dig=yes` digs steps up a slope too steep to walk,
or digs up when there's no way out). Both walk there in short legs (a stop every 6 blocks, walking
first and digging only where walking can't): one long search through diggable ground gave up, or
tunnelled beside the open way and got stuck. When digging is allowed and she makes no progress for 4 s
(a jump bumping a low cave ceiling), she breaks the block over her head and tries again; still for 12 s
without even "moving" (wedged on a block's edge), she steps back to the middle of her block first. A
block she's digging is never given up half dug: by hand terracotta takes 6.25 s, and the route search's
time running out (5 s) used to start the walk over and the same block with it, forever. A dig walk now
waits for the real arrival, finishes the block before ending, and its 20 s limit counts from the last
block broken or block gained. A player in spectator mode is said to be invisible and isn't chased.

| Skill | Parameters | Done when | Stops early when |
|---|---|---|---|
| `go_to` | `target`: `"x y z"`, `"x z"`, `player:<name>`, `mob:<type>`, `block:<type>`, `peak`, `surface`, `looking:<player>` (the block they look at: a floor block is stood on, anything else is gone next to); `range` (0: that exact block; players at least 1, so "next to me" is `player:<name>` range 1) | Within range | No path, not found, target gone (`lost`), attacked (`hurt`) |
| `mine` | `block` (or `log` for any log), `count`, `radius` | Collected `count` of what it drops | None in sight nearby, no tool that can harvest it (says which), unreachable |
| `fight` | `target`: `nearest_hostile` or a mob type; `count` (hostiles: all nearby; other mobs: 1) | `count` dead / no hostiles left | Outnumbered (flees), none found, an animal got away. Never players |
| `explore` | `goal`: `block:<type>` / `mob:<type>` (optional), `radius` | Goal found (says where), or area covered | Night, danger within 8 blocks |
| `eat` | `food` (optional) | Ate | Not hungry, nothing to eat |
| `sleep` | — | Woke up in the morning | Not night, no bed within 32, monsters nearby |
| `craft` | `item`, `count` | Crafted `count` | No recipe, missing ingredients (lists them), needs a crafting table |
| `follow_player` | `player`, `distance`, `seconds` | Time is up | Player out of sight (`lost`), attacked |
| `place` | `block`, `where` (`"x y z"`, default: next to her) | Block is in place | She has none, spot taken, nothing to rest on (steps aside if she's in the spot) |
| `collect_items` | `item` (optional), `radius` | Nothing (of that kind) left | Nothing there, out of reach, inventory full |
| `give` | `player`, `item`, `count` | Tossed at their feet | She has none, player not around |
| `look_at` | `target` (as go_to), `seconds` | Time is up | Target gone (`lost`) |
| `store` | `item` (or `all`: keeps tools, weapons, armour, food, torches), `count` | In the nearest chest/barrel | No chest within 16, chest full |
| `take` | `item`, `count` | Taken | No chest, chest has none, inventory full |
| `smelt` | `item`, `count` | Smelted items taken out | No furnace within 16, no fuel, can't be smelted |
| `wear` | `item` (optional) | The armor is on (named piece, or her best for every slot with nothing better on) | No armor, nothing better than what she wears |
| `hold` | `item` (id, or a kind: `sword` = her best sword; `nothing`) or `slot` 1-9; `hand` main/off | It's in her hand | She has no such item |
| `put_in_hotbar` | `item`, `slot` 1-9 | The item is in that hotbar slot (what was there swaps) | She has no such item |
| `surface` | | Head out of the water, breath back | Nothing reachable within 12 blocks of water, stuck |

All take `time_limit` (seconds, capped per skill). **Doors:** routes go through wooden doors and
gates; she opens closed ones just before reaching them and closes doors she opened once she's
through (unless a player is in the doorway). Iron doors stay walls. Walking skills never break
blocks; only `mine` digs. Shared safety the runner applies to every skill:
`stop` ends it at once; health at 6/20 or lower ends it as `hurt`, then she backs away from the
nearest hostile and eats; death and disconnects end it. Pathfinding never places her blocks
(she'd quietly use up what she mined) and avoids lava. **Breathing** comes before everything:
head under water with air at 10/20 or less, a resumable skill goes on hold while she runs
`surface` (straight up through open water, or swimming through connected water to the nearest air
under a ceiling), any other skill ends as `hurt`, and with nothing running she starts it herself
(`source: "reflex"`; `stop` leaves it be, it ends by itself). The pathfinder plans no routes
through water, so walking along the bottom of deep water fails; she never drowns trying.

### Her own code (stage H, groundwork)

`runProgram(runner, code)` in `src/sandbox.js` runs code she wrote: the body of an async function
that may use `await skills.<name>(params)` (each one goes through the runner like any other skill,
with stop, time limits, low health, modes and breathing; it returns `{ status, reason, data }`, or
status `invalid` for bad params), `state()` (what she sees, as plain data), `log(...)`, `wait(ms)`
(up to 30 s; there are no timers), and two read-only looks at the world: `blockAt(x, y, z)`
(`{ name, solid, x, y, z }`) and `findBlocks(name, { radius, count })` (nearest first; a name
like `_log` matches every kind). It also has single actions, `act.<name>(...)` (`src/actions.js`),
what a move no skill has is built from: `attack(id)` (one swing at `state().nearby[i].id` within 3 m;
no target is off limits, players included), `dig(x, y, z)` (one block within 4.5 m, best tool
equipped; waits a moment for the drop), `place(x, y, z, item?)`, `look(x, y, z)`, `equip(item,
slot?)` and `jump()` (resolves once her feet are a block up, still rising, so placing under her feet right
after climbs; a block under her own feet mid-jump goes in at once, wherever she's looking). Each returns `{ status: 'done' | 'failed' | 'busy', reason, data }`; one at a time, never
while a skill of hers runs, up to 400 per program, and stop cuts one short. Nothing
else: it runs in a separate V8 isolate (`isolated-vm`) with no `require`, files, network,
`process` or timers. `runner.stop()` ends the program as well as its skill; a skill ending `hurt`
or `stopped`, low health, death or a disconnect end it; a time limit (300 s), a CPU budget (2 s of
her code's own work, so an endless loop dies; waiting on skills costs none), 32 MB and 40 skill
calls cap it. The pack's own thread never waits on her code. While a program runs, no one else
can start a skill (her breathing reflex still can). Her learner (stage H1, in the backend:
`app/ai/desktop/practice.py`) writes these programs when she teaches herself a move, and one that
worked becomes one of her moves (stage H2, `app/services/game_skills.py`): her hands pick it like a
skill, `learned_<words>(times)`, and it runs here as a program, `times` times in a row. To try one by
hand, with the server and the pack running:

```bash
bun run program my_program.js            # the file is the body of an async function
bun run program -e "await skills.eat({})"
bun run skill stop                       # stops the program and the skill it's running
```

It prints each skill call with its outcome, the `log` lines and what the program returned. The API
behind it is `POST /programs {code}` and `GET /programs/<id>?wait=<s>`; `GET /blocks?x=&y=&z=&r=`
counts blocks around a spot (the learner compares it before and after a try).

`fight`: creepers first, then skeletons, then zombies, nearest first; best weapon in hand, shield
in the off hand and raised while the swing recharges; hits only at full charge; steps back after a
hit; strafes against skeletons; hits a creeper once, then backs off.
`explore` remembers explored chunks and noted places (water, lava, villages, caves) per world in
`var/explore/`.

### How she moves (motion style layer)

Mineflayer walks her in the direction her head faces, and the pathfinder would snap that direction
to the next step every tick. The motion layer takes over `bot.look`: the pathfinder, mining and
skills only *request* a direction, and each tick (20/s) her head moves toward it:

- **Eased turning:** the turn speeds up gradually (an acceleration limit), cruises below a top
  speed, and slows down as the gap closes; a fast turn can overshoot slightly and come back.
  Looking at things: 260°/s relaxed, 320 focused, 540 combat. Steering along a path is quicker
  (420-600°/s) because the pathfinder needs her within 0.35 blocks of each corner.
- **Reaction time:** a big new target (a mob appears, a block to mine) waits 150-300 ms before she
  starts turning. Corners of a path she's already walking don't.
- **Wobble:** a slow ~1° drift (two sine waves), so her aim is never perfect. In combat a hit only
  lands if her crosshair is really on the mob, and about 1 swing in 12 goes wide.
- **Pace:** relaxed sprints in moods on long straight stretches, focused rarely sprints, combat
  sprints whenever useful. No sprinting through sharp turns; on a turn over 75° she turns first.
  No bunny-hopping: jumps only for real obstacles.
- **Gaze:** looks slightly down at the ground ahead while walking (more when focused), glances at
  mobs and players near her path, and when idle looks around and at players nearby.
- **Styles:** `relaxed` (go_to, explore, follow_player), `focused` (mine, craft, eat, sleep),
  `combat` (fight). The numbers are in `src/motion/styles.js`; tune them there.

`GET /state` shows `motion`: current style, the biggest head turn in one tick so far, snaps (turns
over 35° in one tick: should stay 0) and missed swings.

### Try one by hand

```bash
bun run skill                                     # list skills and their parameters
bun run skill go_to "target=player:<your name>"   # walk to you
bun run skill mine block=log count=3
bun run skill follow_player player=<your name> seconds=60
bun run skill stop                                # stop whatever she's doing
```

### Noticing

Always on, whatever she's doing, no AI: every second or so she checks what she'd mention if she
were playing next to you, and notes each thing **once**:

| Kind | When | Urgency |
|---|---|---|
| `village` | A bell or villager in sight, up to ~96 blocks | heads_up |
| `hostile` | A hostile mob within 16 (seen, or within 6 even unseen; creepers within 10) | urgent when close, else heads_up |
| `ore` | Diamond, emerald, gold or ancient debris in sight within 12 | info |
| `spawner`, `lava` | In sight close by (lava within 3 even unseen) | heads_up |
| `night_coming`, `night`, `morning`, `weather` | Once per day / change | heads_up or info |
| `player_joined`, `player_left` | Someone joins or leaves | info |
| `hurt`, `hungry`, `died` | Lost 4+ health in 3 s or low; food ≤ 6; died | urgent or heads_up |

Each notice has a short description with where it is ("a village (saw a bell), 72 m north-east
(ahead-left)"), the data behind it, and a sequence number. She only notices what she could see
(no diamonds through stone). Places are also saved to her world memory (with water and caves,
quietly). `GET /notices?since=<seq>&wait=<s>` is what Columbina will read in phase 3; `/state`
shows the last few, and the pack log prints them.

### API

All on `http://127.0.0.1:8790` with `Authorization: Bearer <var/api_token>`:

```bash
TOKEN=$(cat var/api_token)
curl -s -H "Authorization: Bearer $TOKEN" localhost:8790/skills                    # the manifest
curl -s -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"skill":"mine","params":{"block":"log","count":3}}' localhost:8790/skills/run  # -> {"id": ...}
curl -s -H "Authorization: Bearer $TOKEN" "localhost:8790/skills/runs/<id>?wait=60"  # waits for the outcome
curl -s -X POST -H "Authorization: Bearer $TOKEN" localhost:8790/skills/stop        # stop now
```

Also `GET /skills/current`, `GET /skills/history`, `GET /notices`, `GET /state` (includes notable
blocks nearby, recent notices, the running skill and motion stats).

## Playing with Columbina (phase 3)

Columbina starts and steers Minecraft like any other game session (start, goal, pause, take over,
end, keep playing). Her hands, the driver model in the backend worker, pick one skill at a time
from this pack's manifest, see each outcome and what she noticed, and report each plan step done.
Urgent notices (a creeper behind her) cut the current skill short so the hands can react;
discoveries (a village, diamonds) and danger wake Columbina so she can tell you.

To turn it on, in `backend/.env`:

```bash
DESKTOP_AGENT_ENABLED=true
MINECRAFT_PACK_OWNER_EMAIL=<the email you sign in to the app with>
```

then `cd backend && uv run alembic upgrade head` once, and restart the backend and the worker
(`uv run python -m app.workers`). The backend reads the pack's token from `var/api_token` by itself.
With the server and the pack running, ask Columbina something like "let's play Minecraft, get some
wood and make a crafting table". The Game session card on the dashboard shows her current skill
and results; Pause and Take over stop her current skill at once. Skills you run by hand
(`bun run skill …`) are never stopped by a session.

## Check

```bash
bun run test     # unit tests
bun run itest    # every skill (and a smoothness check) on a real 26.1.2 server (~2 min; needs the EULA accepted in server/eula.txt;
                 # runs its own world in var/testserver on a free port; SHOW_SERVER=1 to see its log)
bun run smoke    # joins a throwaway 1.21.4 server, reads state, checks the API and viewer
                 # (flying-squid stops at 1.21.4, so the smoke test stays there)
```
