# Desktop agent: connecting the game VM

Runbook for plan §5.1 (`docs/DESKTOP_AGENT_IMPLEMENTATION_PLAN.md`): backend on the Mac, game in
a Windows 11 VM on the Legion. This connects the two. **Nothing here sends input to the game yet**;
that is stage C. After this, the Dashboard's Game session card shows the VM as *connected*.

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
2. In the VM's browser open `http://192.168.1.76:8090/` and download the `.whl` file.
3. In PowerShell, check it is the same file (the hash must match the Mac's):

   ```powershell
   certutil -hashfile $HOME\Downloads\desktop_controller-0.1.0-py3-none-any.whl SHA256
   py -m pip install --user $HOME\Downloads\desktop_controller-0.1.0-py3-none-any.whl
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

To start it at logon (once the manual run works): Task Scheduler → *Create Task* → trigger
*At log on* of your user, *Run only when user is logged on* (input needs the desktop session,
so never a service), action `pythonw.exe` with arguments `-m desktop_controller run`.

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
