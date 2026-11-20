# Jophiel VM connection plan

## Goal

Install and pair one controller per machine. After login, the machine reconnects automatically, even when DHCP changes either endpoint’s address, as long as the host is on a network the user approved for the relay. The user can pick a paired machine by name and can separately authorize Jophiel to start sessions there. Windows, macOS, and Linux should share this experience.

Built on 2026-11-19 (branch `docs/vm-connection-network-boundary`, migration `e3a7c5d9f2b4`); see **Status** at the end. Setup: [infra/desktop/README.md](../infra/desktop/README.md).

## Exposure and the network boundary

Any device that can reach the relay port can open a connection and make the relay process traffic before it knows who is calling. That is already true of today’s setup on port 8765, and a fixed IP does not change it. Automatic discovery would make the relay easier for other devices on the network to find.

The existing protections stay: TLS, a health check that reveals nothing private, pairing or device authentication before any controller traffic, and the backend API on localhost. An unpaired device gets no desktop session, but it can still send connection attempts, consume resources, or reach a bug in the listener. So the plan limits *where* the relay listens, not only *who* it accepts:

- **The user approves a network once.** The approval names one boundary: preferably a private or VM-only interface that only the host and its intended guests can reach (for example a host-only adapter or a virtual switch), otherwise a trusted home LAN identified by its interface and network (gateway identity and subnet, not a single address).
- **The relay follows address changes inside that boundary.** When DHCP moves the host to another address on the approved network, the relay rebinds and re-advertises there without asking again.
- **The relay stays closed everywhere else.** On an unapproved network (café Wi-Fi, a hotspot, a different LAN) it neither listens nor advertises, and the dashboard says why. It never binds a wildcard address.
- **On a shared LAN, the host firewall narrows the port** to the approved subnet (or to known controller addresses where practical), and router port forwarding stays off. The dashboard checks what it can and states what it cannot verify (router settings are outside its reach).

## User flow

1. Start the app and relay on the host. The first time, approve the network the relay may listen on: the dashboard lists candidate interfaces by name, recommends a VM-only interface when one exists, and otherwise offers the current trusted LAN. The dashboard shows when the relay is ready, or that it is closed because the host is on an unapproved network. Normal setup requires no IP field on either machine.
2. Choose **Pair a machine**. On the target machine, install the controller and run the one-time pairing command shown in the dashboard. Complete the OS permission prompts and enable **Start at login** during setup.
3. The dashboard shows the machine name, connection status, and available preview and input controls. Select the machine to start a session manually, or enable **Allow Jophiel to start sessions** for that machine.
4. On later logins the controller finds the relay and reconnects. Jophiel can start an authorized session when the machine is online, even with the dashboard closed. A powered-off VM stays offline. The user can stop a session, disable future automatic starts, or revoke the pairing.

## Implementation order

### 1. Discover the relay and retain its identity

Store the approved network boundary on the host: the interface (by name and hardware identity) and, for a LAN, the network it was approved on (gateway identity and subnet). Extend the [relay](../backend/app/ai/desktop/relay.py) to bind only to that interface’s current address, and only while the host is on the approved network; otherwise stay closed and report the reason. Keep refusing wildcard binds. Advertise a DNS-SD service over mDNS only on the approved interface, once it is listening. When the address changes within the boundary, rebind and refresh the advertisement; when the host leaves the boundary, close the listener and withdraw the advertisement. Keep the backend API on its existing interface. Preserve explicit bind configuration for deployments that use it, still subject to the same no-wildcard rule. If several interfaces are plausible, offer selection by name, recommending a VM-only interface.

On a shared LAN, have setup add or check a host firewall rule that admits the relay port only from the approved subnet, and warn that router port forwarding must stay off. Bound unauthenticated work in the listener: limit concurrent unpaired connections and handshake time so a device on the network cannot tie up the relay.

Update the [pairing API](../backend/app/api/routes/desktop.py) to return the expected relay fingerprint with the one-time command. Extend the [controller](../backend/desktop_controller/src/desktop_controller/) to discover candidate endpoints and verify the certificate fingerprint before pairing. Retain the existing one-time-code proofs and device keys. Store the pinned relay identity separately from the last reachable endpoint. Rediscover on connection loss with bounded retry delays. For networks that block multicast, accept a DNS hostname.

Read existing pairing files as well as the new format. Reuse the saved key and fingerprint when the same relay moves. Moving the backend to a new host requires migrating the relay certificate, private key, and backend pairing records to preserve identity. A replacement identity requires explicit pairing.

### 2. Deliver automatic login and reconnection on Windows

Have setup register the controller for the signed-in user through a Windows logon task. Repeated setup should update that task and launch at most one controller. Start it immediately on first setup. Provide a way to disable startup. Retry transient network failures and stop retrying a revoked pairing. Show connection and permission failures in the dashboard when reachable and locally when disconnected.

### 3. Select machines and authorize Jophiel

Extend the [desktop session panel](../frontend/src/components/DesktopSessionPanel.tsx) to select paired environments by stable ID and display their names. Scope connection state, preview, input, and session controls to the selected ID.

Persist automatic-start permission per owner and environment, initially off for both new and existing pairings. Route Jophiel’s start request through the existing backend session service. At start time, verify ownership, permission, online state, and existing session restrictions. Keep the existing capture and input gates. Deduplicate concurrent starts and expose the initiating actor in session history. Reconnection alone leaves the machine idle.

### 4. Add macOS and Linux behind the same controller interface

Share discovery, pairing, session transport, and status reporting. Isolate screen capture, keyboard and mouse input, permission checks, and login registration behind platform-specific implementations. Start with the current Windows implementation. Add a per-user LaunchAgent on macOS and desktop-session autostart on Linux. Report capture and input availability independently, including when OS permission is missing. Verify X11 and Wayland separately and publish the tested support for each.

## Acceptance checks

- Approve a network, then join a different one (another Wi-Fi or a hotspot). The relay neither listens nor advertises there, and the dashboard says it is closed on an unapproved network. Return to the approved network and it reopens without a new approval.
- With a VM-only interface approved, a device on the host’s LAN cannot reach the relay port. With a LAN approved, the host firewall admits the port only from the approved subnet.

- Pair from a fresh installation without entering an IP. With two relays advertising, only the relay identified in the pairing command succeeds.
- Change the relay’s address, then the VM’s address. Restart both and log in. The same machine returns online without another pairing command or a duplicate dashboard entry.
- Upgrade an existing Windows pairing, block multicast to exercise the hostname fallback, and verify that a different certificate cannot take over a pairing.
- Pair two machines. With no session running, check that preview and input follow the selected machine. The card shows one live session at a time: while one runs (on any machine, including one Jophiel started herself), the card shows that session and names its machine, and the other machines can't be viewed until it ends. That is accepted for now; per-machine live sessions would be a later change. With the dashboard closed, Jophiel can start only on an online machine with automatic starts enabled. Revocation prevents reconnection.
- On each supported desktop, verify login startup, capture, input, missing-permission feedback, session stop, and removal of login startup.

## Status (2026-11-19)

Built, with scripted tests; nothing yet tried on the real VM or across real networks.

- **Relay** (`app/ai/desktop/relay.py` `RelaySupervisor`, `network.py`, `firewall.py`): the approval is `var/desktop_relay/network.json` (interface + subnet, plus the router's hardware address for a LAN); the relay checks every 5 s, binds the interface's current address, rebinds on DHCP moves, closes elsewhere, advertises `_jophiel-relay._tcp` (TXT `fp`, `proto`) only while listening there, and writes `status.json`. A fixed `DESKTOP_CONTROLLER_BIND_HOST` keeps the old behaviour. Unpaired connections: 16 at once, 4 per address, 10 s to say who they are; 64 connections in all. The firewall rule (pf anchor `com.apple/jophiel-relay`, or nftables) is generated and checked by a helper the owner runs with sudo; the dashboard shows the last check and that the router can't be checked.
- **Controller 0.12.0**: discovery + fingerprint-checked pairing, separate identity and last address in the pairing file (old files load), reconnect order saved → mDNS → host name, revocation remembered, login start (Windows logon task / Run key, macOS LaunchAgent, Linux autostart), single instance, `status` / `startup` commands, machine status reported only to relays that ask (`Welcome.features`).
- **Backend**: `GET /api/desktop/relay`, `PUT|DELETE /api/desktop/relay/network` (owner only), `PUT /environments/{id}/auto-start`, pairing returns `pair_args` with the fingerprint. Jophiel's own starts (`desktop_game.start`) need the machine's switch on and the machine online (`desktop_sessions.jophiel_may_start`); her Minecraft pack is not a paired machine and is not gated. Sessions record `started_by`.
- **Dashboard**: machine picker (remembered per browser), machine report, the switch, owner-only relay card, two-OS pairing command, who started a session.

Not covered yet: the mDNS advertisement and browse were not run live (the tests use stand-ins); the macOS / Linux capture and input backends are untested on real desktops; X11 vs Wayland support is as reported by the code, not verified on a machine.
