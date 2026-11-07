# Minecraft ray-based perception and grounded navigation

Design proposal · November 7, 2026

## Purpose

Give the agent a coherent view of the world from her eyes, preserve the spatial evidence needed to recognize openings and obstacles, and connect observed destinations to executable movement. Begin with ray observations over Minecraft geometry. Evaluate how much of that observation should reach the agent as text, images, or both.

This document records the proposed approach. Parameters, interfaces, and performance expectations below require implementation and evaluation.

## Current behavior and evidence

The current implementation provides several separate descriptions of the surroundings.

- `game_packs/minecraft/src/sight.js` checks block visibility with rays and remembers previously seen blocks. The visibility check has no camera field of view. Searches can return both remembered and currently visible blocks.
- `game_packs/minecraft/src/surroundings.js` describes overhead conditions and straight walking directions. Ways extends up to 24 blocks along eight directions. Opening detection checks 16 directions and runs when the overhead classification is sky or trees.
- `game_packs/minecraft/src/terrain.js` provides terrain summaries with a 64-block radius and specialized peak and surface information. The system has several sensing ranges, rather than one unified visual range.
- `game_packs/minecraft/src/skills/movement.js` converts coordinate destinations into pathfinder goals. Ordinary place movement retries up to four times and commonly returns `no path` with remaining distance.
- `game_packs/minecraft/src/world_memory.js` stores visited chunks and noted places. It provides a starting point for persistent landmark memory.
- The current skill registry has movement, interaction, and survival skills. It has no dedicated exploration or scene inspection skill. A saved earlier session manifest included `explore`.

Saved moments in `backend/scripts/mind_plan_moments.json` contain recorded game states from October 19, session `84c556ea`, with reconstructed plans. Those moments were inspected during this discussion. The latest live session history has not yet been audited, and recent failure rates have not been measured.

The user reports repeated unreachable coordinate choices and inadequate recognition of caves and structures. The implementation supports plausible causes for those problems. A straight-direction terrain summary can omit a route that bends around an obstacle. Opening detection can miss an entrance visible across a ravine or from beneath an overhang. A generic navigation failure gives the next decision little evidence about what should change.

## Proposed observation pipeline

```text
Eye position, yaw, pitch, and loaded world geometry
                         ↓
Ray samples over the camera view
                         ↓
Visible surfaces, depth boundaries, and unknown regions
                         ↓
Scene regions and stable landmark references
                         ↓
Candidate approaches evaluated by navigation
                         ↓
Text observation and optional aligned visual views
                         ↓
Agent selects a skill and a grounded target
                         ↓
Movement or inspection produces another observation
```

Keep the numerical observation available to code regardless of the agent-facing format. This allows text and image variants to be compared using the same underlying evidence.

## View geometry and ray sampling

Anchor every observation to the actual eye position, yaw, and pitch. Record the observation time, dimension, camera parameters, and a unique observation identifier.

An initial experiment can use a 100-degree horizontal field of view, a 64 × 36 sampling grid, and a maximum forward range of 128 blocks. These are candidate settings. The available loaded terrain and measured processing cost constrain the usable range. Vertical field of view must be derived consistently from the camera projection and aspect ratio.

Use a perspective projection so grid cells correspond consistently to view directions. For each ray, retain its direction, the first observed surface, distance, world position, block identity, and surface normal when available. Define distance consistently as distance along the ray from the eye. Any derived depth image must document its encoding.

Use coarse sampling across the view and investigate denser sampling near depth discontinuities, narrow gaps, and candidate entrances. Sampling density and maximum work per observation need explicit bounds so perception cannot stall movement.

Treat transparency, partial block shapes, vegetation, glass, and water deliberately. A single material rule may be insufficient when a transparent foreground surface and a surface behind it are both relevant. Preserve limited additional hits where needed and document which surface each visual layer displays.

Keep unloaded terrain distinct from observed empty space. Distinguish a surface hit, termination at unknown terrain, and no hit before the maximum range. Label sky only when there is sufficient evidence to establish it. An empty ray alone does not establish sky.

Maintain a separate local safety observation for immediate ground, collisions, and hazards. Track its source separately from the forward visual observation. Lighting and darkness also need an explicit visibility policy, since the existing block visibility implementation ignores darkness.

## Recognizing the scene

Group related visible samples into regions such as walls, ledges, water, openings, and constructed surfaces. Preserve the underlying samples so an imperfect semantic label does not erase useful spatial evidence.

### Cave entrances and passages

Recognize candidate openings from visible geometry wherever the agent stands. Relevant evidence includes a rock boundary, empty space behind the boundary, apparent entrance dimensions, a visible floor, and a continuation that slopes or turns away.

The current overhead classification must not gate this detection. An entrance can be visible while the observer is under trees, beneath a roof, inside a cave, or across water.

Represent uncertainty explicitly. A recess, gap between surfaces, and deep cave may initially look similar. Report a candidate opening and its visible extent, then inspect from another viewpoint when necessary. A passage beyond a bend remains unexplored until observed.

Example observation

> A broad opening is visible beneath the cliff, about 45 blocks ahead-left. Its floor slopes down. The interior disappears behind a bend.

### Structures

Group visible walls, roof surfaces, doorways, and windows into possible structures. Use exact block identities as evidence, while preserving uncertainty about the whole object.

Begin with a limited set of useful feature types. Evaluate partial occlusion and unfamiliar constructions before relying on specific building classifications.

Example observation

> Part of a wooden structure is visible behind the trees. A doorway has not been observed.

### Observation and memory

Maintain stable landmark identifiers across observations. Each landmark should retain its observed position or extent, supporting evidence, confidence, last observation time, and visible or remembered status.

Associate landmarks within the correct world and dimension. Reconcile moved, removed, or changed surfaces when new evidence arrives. Prevent references from an older observation from silently resolving to a different object.

Remember which approaches were attempted, under which movement constraints, and what failed. A failed approach does not establish that every route to the landmark is impossible.

## Agent-facing representation

### Compact text

Describe orientation, salient visible regions, important changes, uncertainties, and grounded target references. Keep the complete numerical grid accessible to code for measurements and inspection.

Example

```text
Observation 182. Facing northeast, looking 8 degrees downward.
View spans 100 degrees horizontally. Maximum observation range is 128 blocks.

A — opening beneath the cliff, approximately 43 blocks ahead-left.
    Floor visible inside. Interior turns out of sight.
    Ravine separates you from the entrance. Access unconfirmed.
B — viewpoint along the near rim, approximately 9 blocks left.
    Navigation currently finds a walking route to B.
```

Descriptions should distinguish direct observation, interpretation, memory, and navigation results. Keep ordinary task updates compact and send meaningful changes as movement reveals new information.

### Aligned material and depth images

Render two views from the same rays for the visual experiment.

| View | Encoding | Intended use |
| --- | --- | --- |
| Material | Stable colors for material categories with a legend | Preserve surface arrangement and broad material differences |
| Depth | Documented distance scale with a fixed legend | Preserve obstacles, gaps, and depth discontinuities |

Use distinct encodings for unknown terrain and rays with no surface hit within range. Avoid changing the distance scale silently between frames. Place a few stable landmark labels on both views and reference them in the accompanying text.

Retain exact block names and numerical distances in the underlying observation. Labeling every image cell would reduce readability. A 64 × 36 grid already contains 2,304 samples, so serializing every block name and distance into the agent prompt is unlikely to be a practical default.

A synthetic image requires interpretation. The model's ability to understand the palette, depth scale, and small openings must be measured. The combined image and text format is a hypothesis to test.

### Delivery decision

Build a shared observation representation that supports all evaluation formats. Compact text is the initial latency-conscious integration path. Keep image delivery configurable for the comparison and for ambiguous inspection tasks. Select the routine format after measuring decision quality, latency, and input cost.

## Grounding observations in movement

Seeing a destination and finding a route to it are separate pieces of evidence. The scene model should retain both.

Allow skills to reference observed landmarks and candidate viewpoints. Conceptual requests such as `inspect(A)` and `go_to(B)` represent intended interfaces, not existing supported syntax.

Navigation resolves a landmark into valid standing or interaction positions. It evaluates approaches using the same movement constraints used during execution, including clearance, permitted drops, water behavior, and whether digging is allowed. It can propose a reachable intermediate viewpoint when the complete approach remains unknown.

Preserve raw coordinate support with validation. Avoid treating a visible surface point as automatically suitable for standing. A landmark may have several candidate approaches, and an unreachable nearest point should not prevent considering the others.

A route found during planning remains subject to execution changes. Revalidate as movement reveals terrain or the environment changes. Unknown terrain should remain explicit in planning and outcomes.

Useful movement outcomes include an invalid standing position, a route blocked under the current movement constraints, an incomplete search, terrain beyond current knowledge, and progress to an intermediate viewpoint. Only report a specific obstacle when evidence supports it.

Replace repeated unchanged attempts with bounded recovery that changes the approach or obtains new evidence. Preserve the distinction between a search exhausting its budget and establishing that no route exists within the searched space.

## Looking, exploring, and decision timing

Connect looking actions to refreshed observations. Support bounded inspection of a landmark or direction and scanning through several views. Movement should also reveal and report new features.

The perception and movement loops can update frequently while a skill runs. The agent chooses goals and skills at meaningful decision points, such as arrival, a new relevant discovery, an unresolved obstacle, or danger. Ordinary path following and local replanning remain responsibilities of the execution layer.

Example sequence

1. The agent observes opening A across a ravine.
2. Navigation identifies viewpoint B along the near rim and finds a route to it.
3. The agent chooses to investigate A through B.
4. Movement reaches B and refreshes the observation.
5. New evidence reveals a possible crossing or establishes the need for another approach.

This sequence grounds exploration in observed places and executable movement while preserving the agent's choice of purpose.

## Relationship to YOLO and image recognition

The initial approach uses ray observations and existing block geometry. A detector can later contribute semantic recognition to the same scene representation if structure recognition remains inadequate.

If evaluated, an image detector would need appropriate Minecraft categories, representative data, measured runtime, and a way to associate detections with world geometry. The skill-selecting agent could consume structured detections as text. Image delivery to the agent and use of an image detector are independent architectural choices.

An additional action-producing model is not required for the proposed first implementation. The existing agent selects skills, while the controller executes movement and interactions.

## Evaluation

Compare observations from identical recorded viewpoints using three formats.

1. Text observations alone.
2. Material and depth images with minimal orientation and legend information.
3. The same images with concise text and labeled targets.

Keep the underlying ray evidence, task, available skills, model settings, and navigation constraints consistent. Measure recognition separately from route selection. Then run closed-loop trials where actions change the viewpoint, since recorded observations alone cannot establish navigation success.

| Scenario | Behavior to evaluate |
| --- | --- |
| Entrance visible from beneath an overhang | Recognizes the opening regardless of overhead classification |
| Cave across a ravine | Recognizes the destination and preserves uncertainty about access |
| Destination around a wall | Investigates or follows a bending route |
| Narrow opening between sampled rays | Measures detection sensitivity to grid resolution |
| Structure partly obscured by trees | Reports visible evidence with appropriate uncertainty |
| Passage turning out of sight | Requests another viewpoint and preserves unknown continuation |
| Unloaded terrain ahead | Reports unknown space correctly |
| Darkness, glass, water, and partial blocks | Applies the chosen visibility policy consistently |
| Previously observed landmark behind the agent | Distinguishes memory from current visibility |
| Failed approach followed by a retry | Changes approach or gathers new evidence |

Measure opening and structure recognition precision and recall, grounded target selection, invalid destination frequency, repeated unchanged failures, successful arrival, and useful inspection choices. Record perception processing time, decision latency, input size, and end-to-end task completion time.

Define acceptance thresholds after collecting a baseline from the existing system. The latest session audit should supply representative failures for this benchmark.

## Implementation sequence

1. Audit recent sessions and capture representative failure scenes. Preserve a baseline of current observations and navigation outcomes.
2. Improve destination validation and navigation outcomes so failures carry actionable evidence.
3. Add the bounded ray observation representation with camera geometry, unknown-space handling, and configurable sampling.
4. Add visible region grouping, candidate openings, and stable landmark references. Reuse existing memory where its semantics fit.
5. Connect landmark references to candidate standing positions, approach evaluation, and refreshed observations after movement or looking.
6. Add aligned material and depth renderers and run the three-format comparison.
7. Select the routine delivery format and tune observation frequency from measured quality and latency. Evaluate additional semantic detectors only against remaining recognition gaps.

## Decisions still requiring experiments

- Field of view, sampling resolution, maximum range, and adaptive sampling budget.
- Lighting and transparent-surface behavior.
- Recognition confidence thresholds and landmark matching rules.
- Which changes warrant an agent decision or an image observation.
- Candidate approach search budgets and recovery behavior.
- Whether visual input improves decisions enough to justify its measured cost.

## Implementation status (November 7, 2026)

Built in the Minecraft pack, with the backend prompts updated. Steps 2 to 5 of the sequence are in place, step 6 has its renderers but no comparison yet, and steps 1 and 7 have not been done.

| Piece | Where | What it does now |
| --- | --- | --- |
| Ray view | `src/view.js` | 100° × 67.7° perspective grid, 64 × 36 rays, 128 m, plus up to 1,200 refined rays near depth jumps and cover entries. Block state id lookups with per-view column cache. Budget of 400,000 lookups, reported when hit. Ends: surface, unknown (unloaded or below the world), sky (past the build limit), clear (nothing within range), murk (under water past 24 m), skipped. About 25 ms per view on the flat test server. |
| Visibility policy | `src/view.js` | Solid, partial blocks, logs and leaves stop sight. Air and collisionless plants pass. Glass, panes, iron bars and barriers are seen through and noted. Water from above is a surface with the bottom within 12 blocks kept. Under water, sight passes through water up to 24 m. Lava stops sight. A surface with sky light and block light both 0, farther than 16 m, keeps its geometry and loses its block name. |
| Cover and openings | `src/view.js`, `src/scene.js` | For each surface hit, cover means a solid or glass block (not trees) within 16 blocks above the air in front of it. A ray that passed from uncovered air into covered air and saw at least 2.5 blocks past the change counts toward an opening. Rays are grouped by entry cell. Each group reports width, height, roof block, built or natural roof, depth seen inside, floor and slope from ground-supported cells, darkness, and the ground on the straight line to it. The overhead classification does not gate this. |
| Passages, structures, liquids, unknown | `src/scene.js` | Passages: grid rays from inside covered space that travel 14 m or more, grouped in the image. Structures: hits and glass passes on built materials, grouped in 3D, with partly-behind-trees from nearer leaf neighbours. Water bodies of at least 10 rays. Any lava. Unloaded terrain by image third. |
| Landmarks | `src/landmarks.js`, `src/world_memory.js` | Ids `L1`, `L2`, … never reused and saved with the world memory. Matching is by kind, dimension and distance (structures also by box overlap). A closer view replaces position and facts. Remembered landmarks whose place is loaded and checkably changed become gone. Approaches and route checks are kept per landmark. |
| Freshness and memory | `src/perception.js` | A new view when she moves 0.75 blocks or turns 4°, or after 2 s, and in the background every 1.5 s, backing off after a slow view. New clear openings, structures and lava become notices. Ray hits on ores, logs, water, beds and tables count as seen for block searches. |
| Agent-facing text | `src/perception.js`, `src/describe.js` | "In view" (up to 8 landmarks) and "Remembered places" (up to 6) lines. Each line separates what was seen, what is remembered, and route results with their age and origin. |
| Grounded movement | `src/navigation.js`, `src/skills/movement.js` | Coordinate targets are checked for a standing spot. A bad spot is adjusted to the nearest one within 3 blocks, or refused without walking. A route check under the walking rules runs in 15 ms slices with a 2.5 s budget (4 s with digging). `no_route` ends at once, after moving to the closest reached spot when that is at least 3 m nearer. `incomplete` is reported as not proof. Legs continue only while she gets closer. Landmark targets try standing spots in order (an opening's mouth first). Failures carry `data.nav` with problem, reached spot, remaining distance and route result. |
| Looking | `src/skills/looking.js` | `look_around` turns through 4 views, or toward a landmark, side, compass direction or point. It refreshes landmarks and checks walking routes to the nearest three. |
| Images | `src/view_image.js`, `GET /view/image` | Material view with fixed colours and face shading. Depth view on a fixed square-root scale with distinct colours for clear, sky, unknown and murk. Landmark labels are drawn on both. Not sent to any model. |
| Backend | `skill_driver.py`, `mind_minecraft.py`, `tick.py` | Hands rules for landmarks, `look_around` and acting on go_to evidence. The mind's senses and shape lines include the view. Tick news when a landmark is first seen. |

Tests: `test/view.test.js` (17 unit tests over hand-built worlds) and `test/integration/view.itest.js`. The integration test, on a real server, sees a mouth across a 9-deep ravine. `go_to L1` then fails as `no_route` in about 4 s, at the rim, naming the drop. Coordinates inside rock are refused without walking. A guessed height is put on the ground. After a bridge is added, `go_to L1` arrives at the mouth.

Not done: the session audit and baseline (step 1), the three-format comparison and any image delivery to a model (step 6), tuning from measurements (step 7), and the closed-loop scenario table above. Known limits: a one-block gap at 30 m can fall between grid rays when it causes no depth jump. A sloping floor beyond a lip is visible only from close. Landmark memory is keyed by server address, so two worlds served on the same address share it until changed places are checked as gone.
