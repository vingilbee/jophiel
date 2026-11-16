# Desktop agent: connecting the game VM

Runbook for plan §5.1 (`docs/DESKTOP_AGENT_IMPLEMENTATION_PLAN.md`): backend on the Mac, game in
a Windows 11 VM on the Legion. This connects the two. After this, the Dashboard's Game session card shows the VM as *connected*, with
an optional live preview of the VM's screen.

The VM itself (VMware settings, local account, isolation, Minecraft settings) is set up as in
plan §5.1 steps 1-4.

## 1. Mac: turn it on

1. Make the Mac's LAN address stable (plan §5.1 step 6): System Settings → Wi-Fi → your network →
   *Private Wi-Fi address* **Fixed** (or Off), plus a DHCP reservation on the router. This guide
   assumes `192.168.1.76`.
2. In `backend/.env`:

   ```bash
   DESKTOP_AGENT_ENABLED=true
   DESKTOP_CONTROLLER_BIND_HOST=192.168.1.76   # LAN address only; the relay refuses 0.0.0.0
   ```

3. Database and containers (from `app/`):

   ```bash
   docker compose up -d db redis            # re-creates them bound to 127.0.0.1 only
   cd backend && uv run alembic upgrade head
   ```

4. Run the backend as usual (it stays on 127.0.0.1:8000), and the relay in its own terminal:

   ```bash
   cd backend && uv run python -m app.ai.desktop.relay
   ```

   The first run creates the relay's certificate in `backend/var/desktop_relay/` and prints its
   fingerprint. macOS asks whether Python may accept incoming connections: **Allow**.
   Never forward port 8765 on the router.

## 2. Mac: hand the controller to the VM

The VM has no shared folders or clipboard (on purpose), so it downloads the package from the Mac
over a short-lived file server.

```bash
cd backend
uv build --package desktop-controller --wheel -o /tmp/columbina-controller
shasum -a 256 /tmp/columbina-controller/*.whl          # note this hash
python3 -m http.server 8090 --bind 192.168.1.76 --directory /tmp/columbina-controller
```

Stop the server (Ctrl-C) as soon as the VM has the file.

## 3. VM: install

1. Install Python 3.12 or newer from python.org (tick *Add python.exe to PATH*).
2. Download the `.whl` file into the VM. Don't open it (Windows has no app for it; pip installs it
   below). From PowerShell, which avoids browser download prompts:

   ```powershell
   curl.exe -o $HOME\Downloads\desktop_controller-0.11.0-py3-none-any.whl http://192.168.1.76:8090/desktop_controller-0.11.0-py3-none-any.whl
   ```

   (Or open `http://192.168.1.76:8090/` in the VM's browser and save the file.)
3. In PowerShell, check it is the same file (the hash must match the Mac's):

   ```powershell
   certutil -hashfile $HOME\Downloads\desktop_controller-0.11.0-py3-none-any.whl SHA256
   py -m pip install --user --force-reinstall $HOME\Downloads\desktop_controller-0.11.0-py3-none-any.whl
   ```

## 4. Pair (once)

1. Dashboard → Game session → **Pair game VM**. The card shows a command with a one-time code
   (valid 10 minutes).
2. Run it in the VM:

   ```powershell
   py -m desktop_controller pair 192.168.1.76:8765 ABCD-EFGH-JKMN
   ```

   It prints `paired with environment …` and the relay fingerprint, which should match the one
   under *Relay fingerprint* on the card. The pairing is saved in
   `%APPDATA%\Columbina\controller.json` (it holds the device key; don't copy it anywhere).

## 5. Connect

```powershell
py -m desktop_controller run
```

It prints `connected (generation N)` and the card turns *connected* within ~5 s. It reconnects on
its own if the Mac or network goes away, and stops for good (with a reason) if the VM is removed
in the app, paired again elsewhere, or the relay's certificate changes.

### The preview

The card's **Show preview** button turns on capture; it is off until you press it. Choose
what Columbina can see:

- **Whole VM screen** (default): everything on the VM's screen, about two pictures a second.
  Press **I'm signing in** before typing a password in the VM; capture stops inside the VM until
  you press **I'm done signing in**, and the card says when the VM has confirmed it. Windows'
  own sign-in and permission prompts are never captured.
- **Game window only**: just the game's window, and only while it is the active window. A
  launcher login or browser in front means no picture, even without the button.

Pictures are never saved: the Mac keeps only the latest one, in memory, for 10 seconds. The
preview needs Redis (`REDIS_URL`, already in `.env`).

### Jophiel's own desktop (general desktop control)

With the controller running (0.10.0 or newer) and the preview on (**Whole VM screen**), the VM is
Jophiel's own Windows desktop: in chat she has `screenshot`, `move_mouse`, `click`, `drag`, `scroll`
and `keyboard`, and uses them when you ask her to do something there ("open Notepad and write a
haiku"). No session to start or end. Input goes to whatever is in front, as yours would.

- The preview switch is also her switch: preview off, or **I'm signing in**, and every action is
  refused ("Not done"). Nothing is pressed while a Windows sign-in or permission prompt is in front.
- Apps running as administrator can't be clicked or typed into unless the controller itself runs
  as administrator (Windows blocks it; she's told "Windows blocked the input").
- Dry run on any machine: `py -m desktop_controller run --fake-input` prints what she would press.
- Each action becomes experience on the Mac: a `desktop_action` capture record on the turn's chain,
  and a `desktop.desktop_action` observation (System page → hands; `GET /api/memory/observations`).

### Security monitoring (her desktop's security logs)

With the controller running (**0.11.0 or newer**), the card has its own **Security monitoring** switch,
separate from the preview and from her hands. While it's on, the VM reads its Windows event logs **read
only** (`wevtutil gli` / `wevtutil qe`; it never clears, writes or reconfigures a log) and sends them to the
Mac; Jophiel's mind looks into anything the logs themselves mark as notable, takes a periodic look, and
in chat she has `get_security_activity`. Switching it off stops reading at once; switching it on again
starts at the logs' end, so nothing from while it was off is collected.

- **One-time setup on the VM**: the Security log needs the controller's Windows user in the
  **Event Log Readers** group. In an administrator PowerShell:
  `Add-LocalGroupMember -Group "Event Log Readers" -Member $env:USERNAME`, then sign out and in again.
  Without it the card lists *Security: access denied* (the other channels still work).
- The channels read are `SECURITY_CHANNELS` in `backend/.env` (default: Security, System, Defender,
  Firewall, PowerShell, Sysmon, Task Scheduler, RDP, WinRM, SMB server). A channel the VM doesn't have
  (Sysmon unless installed) shows as *not on this machine*; that's expected. For richer process and
  network records, install [Sysmon](https://learn.microsoft.com/en-us/sysinternals/downloads/sysmon)
  in the VM; it is picked up with no change here.
- Bookmarks live next to the pairing (`%APPDATA%\Columbina\security_state.json`): after the VM or
  the Mac was away, it recovers what the log still holds and reports what it lost as a gap.
- Dry run on any machine: `py -m desktop_controller run --fake-security` reports made-up records.
- On the Mac the relay must be running (it carries the records). `backend/var/` logs nothing extra;
  records are observations (`desktop_security`), visible with `GET /api/memory/observations`.

### Test controls (stage C)

Start a session on the card with the game open **and in front** in the VM. The session turns
*Playing* once the VM finds the game window. The card's **Test controls** then send hand-written
moves (walk, jump, turn, mine, …). Keys only ever go to the game window: click any other window in
the VM and the VM lets go of everything and the session pauses ("not_foreground"). Pause, Take
over, and End stop input within a second; if the Mac or the network goes away, the VM lets go
on its own within 1.5 s.

For menus (inventory, crafting): set the preview to **Game window only**, open the menu in the
game, then click the picture on the card. The card shows `game: menu` or `game: playing`; clicks
are refused while playing, because moving the pointer then would swing the camera.

### Letting Columbina drive (stage D1)

The driver runs in the backend worker (`uv run python -m app.workers`, already in mprocs) and uses
`OPENAI_API_KEY`. On the card, with a session playing and the preview on, open **Give Columbina a
goal**, write a goal and a few steps (`what to do | when it is done`), and press **Start
driving**. She plays until the steps are done, she gets stuck, or you press Pause. Each step is
one model call (about 1-2 s); timings go to `backend/var/desktop_driver.jsonl`.

To start it at logon (once the manual run works): Task Scheduler → *Create Task* → trigger
*At log on* of your user, *Run only when user is logged on* (input needs the desktop session,
so never a service), action `pythonw.exe` with arguments `-m desktop_controller run`.

## Updating the VM program

When the program changes, repeat §2 and §3 with the new file name (the version is in it,
e.g. `desktop_controller-0.11.0-py3-none-any.whl`), then restart `run`. The pairing is kept;
no new code is needed.

## 6. Check what the VM can reach (stage B gate)

From PowerShell in the VM:

```powershell
curl.exe -sk https://192.168.1.76:8765/health      # {"status":"ok",...}       relay: reachable
curl.exe -s  http://192.168.1.76:8765/health       # fails                      no plain HTTP
curl.exe -s  http://192.168.1.76:8000/api/capabilities --max-time 3   # fails  API not on the LAN
Test-NetConnection 192.168.1.76 -Port 5432          # TcpTestSucceeded: False   Postgres
Test-NetConnection 192.168.1.76 -Port 6379          # TcpTestSucceeded: False   Redis
```

## Turning it off

- **Remove this game VM** on the card: the VM is disconnected within ~2 s and its key stops
  working. Pair a new environment to use it again.
- Stop the relay: the VM shows *can't reach the relay* and keeps retrying; the card shows
  *not connected*.
- Emergency: power off or suspend the VM in VMware on the Legion.
- New relay certificate: stop the relay, delete `backend/var/desktop_relay/`, start it again, and
  pair every VM again (they refuse the new certificate until then).
