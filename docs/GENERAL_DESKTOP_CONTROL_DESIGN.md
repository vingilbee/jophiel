# General desktop control for Columbina

Design for the dedicated Windows VM on `codex/general-desktop-control`.

## Scope

Use the paired Windows VM as a general desktop. Columbina can switch apps, open files, browse, and interact with ordinary Windows interfaces using the same visible controls, keyboard shortcuts, and mouse gestures a person would use.

Replace the VM's game-specific tools, profiles, and gameplay driver with direct desktop tools. Keep the separate Minecraft pack tools for Columbina's in-game player.

## Where it runs

1. **Orchestrator.** Columbina receives screenshots as images, chooses the next action, and checks the result when needed. The desktop tools are direct orchestrator tools.
2. **Backend relay.** Reuse the paired connection to carry screenshots, input commands, and action results between the backend and the Windows guest.
3. **Windows controller.** This is her hands for the VM. It captures the screen and executes each requested keyboard or mouse action. The orchestrator owns all decisions about what to do next.

## Tools

| Tool | Behavior |
| --- | --- |
| `screenshot` | Take a fresh screenshot of the VM's primary screen. Optionally return a crop for inspecting small text or controls. |
| `move_mouse` | Move the pointer to a point and leave it there for a hover menu or tooltip. |
| `click` | Click a point with the left, right, or middle button. Support double-click and modifiers such as Shift. |
| `drag` | Hold a mouse button, move between points, and release. Support actions such as selecting text, moving a window, and dragging a file. |
| `scroll` | Scroll vertically or horizontally at a point. Support modifiers for gestures such as Ctrl+scroll. |
| `keyboard` | Type text, press keys, or send Windows and app shortcuts such as Alt+Tab and Win+E. |
| `run_command` | Run one shell command and get its output back as text (2026-11-24; see "Commands" below). Offered only when the controller reports it can. |

Coordinates refer to the screenshot used to choose the action. The tool layer maps them to Windows screen coordinates, accounting for screenshot scaling and crop offsets. To change an app's zoom, use its keyboard shortcut, scroll gesture, or visible controls. A screenshot crop only changes the image Columbina sees.

## What Columbina sees

The current Windows controller captures JPEG screenshots. Today, the VM's chat look tool generally sends a frame to a separate vision model and returns a text description. In the new flow, the orchestrator receives the screenshot itself so it can locate controls and verify the effect of an action. Browser pages are seen as pixels, like any other Windows app.

## Connection and action lifecycle

- The VM remains connected while its paired controller is online. Desktop tools are available when desktop access is enabled.
- Use ordinary tool calls throughout a task. There is no game session to start, goal state to maintain, or automatic pause when the task is complete. Columbina simply stops issuing actions and the connection stays ready for the next request.
- Every input call is bounded. It releases any keys or mouse buttons it held before returning, including when execution fails. Return an execution result so Columbina can distinguish a delivered action from one that failed or could not be confirmed.

## Existing code

- [VM setup and pairing](../infra/desktop/README.md)
- [Orchestrator tool registration](../backend/app/ai/chat.py)
- [Current game tools](../backend/app/ai/tools/desktop/tools.py)
- [Backend relay](../backend/app/ai/desktop/relay.py)
- [Windows capture](../backend/desktop_controller/src/desktop_controller/capture.py)
- [Windows input](../backend/desktop_controller/src/desktop_controller/input_windows.py)

## Implementation (2026-11-07)

| Piece | Where |
| --- | --- |
| Action contract, bounds, release-on-failure, sign-in prompt guard | `backend/desktop_controller/src/desktop_controller/desktop.py` (`DesktopHands`) |
| Windows input (VK codes, Unicode typing, whole-desktop mouse, wheel) | `input_windows.py` (`WindowsDesktopInput`) |
| Screenshots (primary screen or a zoomed region, what's in front) | `capture.py` (`WindowsCapture.screenshot`, `desktop_shot`) |
| Wire messages | `link.py`: `DesktopAction` (relay → VM), `DesktopResult` (VM → relay), frame kind `screenshot` |
| Relay forwarding | `backend/app/ai/desktop/relay.py` (`_SessionLink.desktop_action` / `desktop_result` / `accept_screenshot`) |
| One action there and back, access, experience records | `backend/app/services/desktop_control.py` |
| The six tools, coordinate mapping, the picture hook | `backend/app/ai/tools/desktop_control/tools.py` |
| Prompt section | `backend/app/ai/prompts.py` (`_DESKTOP_PROMPT`) |

**Flow.** Tool → `desktop_control.act` → Redis stream `desktop:commands:<env>` (with a deliver-by time; the relay
drops anything older, so input is never pressed late) → relay → controller → `DesktopResult` (+ a `screenshot`
frame before it) → Redis `desktop:act:<id>:result` / `:shot` → the tool. No answer within the action's own bound
plus a margin is `unconfirmed`. Results: `done` (delivered), `failed` (Windows refused part of it; everything held
was released), `refused` (nothing sent), `unconfirmed`.

**Access.** Tools and the prompt section appear on reply turns when `DESKTOP_AGENT_ENABLED` and the user has a
paired VM. Each action is refused while the controller is offline, the preview gate isn't `allowed` (off, or
"I'm signing in"), or a game session on the VM holds the input. Background turns (game events, nudges) never get
the desktop tools.

**What she sees.** Screenshots go to the orchestrator as images: OpenAI chat models don't read images inside tool
results, so a LangGraph `pre_model_hook` (`screenshot_hook`) adds each screenshot's picture in a message right
after the tool answers it belongs to. Only the newest 3 are shown; the conversation state is unchanged and nothing
is persisted. Each screenshot has an id (`s1`, `s2`, ...); points in every tool are pixels of the latest one, or of
the one named. A crop is a new screenshot with its own pixels and offset.

**Bounds.** Per action: text ≤ 2000 chars, ≤ 20 key presses, chords ≤ 5 keys, drag ≤ 5 s and ≤ 12 points, hover
≤ 5 s, scroll ≤ 30 notches. Per turn: ≤ 80 actions, ≤ 40 screenshots (graph recursion limit raised to match).

**Observation.** Every action is an observation, source `desktop`, kind `desktop_action` (what she meant, in
code's words; the status; what was in front after). Memory keeps failures and unconfirmed actions
(`action_failed`, repeats coalesce) and a new window in front (`desktop_seen`); the rest stays an observation.
It binds to the context in focus when it happened.

**Learning.** Every action is a learning-capture execution of kind `desktop_action` on its turn
(migration `c5f9a2d7e3b1`), so the turn's chain and a retrospective assessment of the exchange show what her hands
did (one `desktop:` fact per turn). A failed or unconfirmed action opens an `execution` assessment of that exchange
(`desktop:<turn>`), with each failed action attached as evidence.

**The VM is her desktop, not a game machine.** `desktop_game` counts a paired VM as a game environment only while it
still has a game to play there: with her Minecraft pack, `minecraft_java` on the VM is retired
(`game_pack.REPLACED_BY`), so a Minecraft-only VM gets no game summary or game tools, only the desktop tools. A VM
allowed another game profile (or a user without the pack) keeps the old game path as well. The VM gameplay driver,
game profiles and stage C/D card controls are still in the code (a VM session started from the card blocks the
desktop tools while it runs); removing them is a separate cleanup.

**Not yet.** Lessons from desktop assessments aren't read by the orchestrator (they reach Student 2 like other
lessons). No screenshot after each action automatically; she takes one when she needs to check.

## Commands (2026-11-24, controller 0.13.0)

Terminal work through the screen failed in a way that matters: on Kali she ran `ip addr` and `nmap -sn` four times
in one turn, because each result was only pixels, and she sees the newest 3 screenshots (older ones keep a line
with nothing about what they showed). `run_command` returns the output as text that stays for the whole turn.

- **Controller** (`desktop_controller/commands.py`, `RunCommand` in `desktop.py`): one command in the machine's
  shell (`bash -c`, else `sh -c`; PowerShell on Windows), from the home folder, input closed (a password or
  confirmation prompt gets end-of-file instead of hanging; sudo only without a password), colours and pagers off,
  its own process group. `timeout_s` 1-600 (default 60); past it, or when the connection closes, the command and
  what it started are stopped, and the output so far comes back (`failed`). Output is stdout and stderr together,
  cleaned (escape sequences, redrawn progress lines), at most 12,000 characters: start and end, cut in the middle
  (`output_cut`). `DesktopResult` carries `exit_code`, `output`, `output_cut`.
- **Offered** only when `ControllerStatus.commands` says it's available (0.13.0+; an older controller doesn't say,
  so no tool), the desktop is usable, and the user's view switch is on (relay and controller both refuse
  otherwise, like every action). It doesn't touch the screen, so there's no sign-in-prompt check.
- **Recorded**: the learning capture (owner-only) keeps the command and the last 4,000 characters of output; the
  observation for memory says only "ran a command (exit code N)", never the command or its output; her hands'
  trail (Redis, 1 h) has the command and the last 600 characters, so her background mind (`services/mind_desktop.py`)
  reads how the output ended; the System page's hands part shows the exit code and what it printed.
- **Prompt**: with commands available, terminal work goes through `run_command`, its output read before running
  anything else; screen tools for apps with windows. The desktop line names the OS the controller reports (no
  longer "Windows" for every machine).
- **Her mind on the desktop** (2026-11-24): turns with the desktop tools run on `desktop_control_model`
  (`gpt-6-luna`, was `gpt-6.2-sol` until 2026-11-24) with `desktop_control_reasoning_effort` (`low`); other turns keep the quick model with no
  reasoning. Chat Completions refuses function tools with reasoning for these models (luna and sol alike), so such a
  turn goes through the **Responses API** (`agent.get_chat_model(model, reasoning)`): its reasoning comes back
  encrypted and is passed back on the next call (`store=False`, nothing kept at OpenAI), so what she worked out at
  one step is still hers at the next. Specialists (sub-agents) keep the turn's usual model. sol refuses
  `reasoning_effort: "none"`: keep an effort set while it's the desktop model.
- **Updating a machine**: the controller on the VM needs 0.13.0 (reinstall it there); until then she has the screen
  tools only.
