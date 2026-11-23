# Phase 8 backend contract, as built (2026-11-22)

Final names for the phone side of [`frontend-phone/PLAN_PHASE_8.md`](../../frontend-phone/PLAN_PHASE_8.md). Where
this differs from the plan, this file wins. How it works inside the backend: "Phase 8" in
[`MIND_ARCHITECTURE.md`](MIND_ARCHITECTURE.md).

## Notifications

Socket frame `{"type": "phone_notifications", ...}` while live, else `POST /api/phone/notifications` (bearer,
phone session; same body, `type` optional). Response `{"posted": n, "removed": n}`; `422` without echoing the body.

```json
{
  "posted": [{
    "key": "n_8f3a…", "revision": 3,
    "app": "WhatsApp", "package": "com.whatsapp",
    "title": "Rina", "text": "are you coming tonight?",
    "conversation": "Rina", "conversation_id": "c_77d…", "conversation_identity": "shortcut",
    "is_group": false,
    "messages": [{"event_id": "e_a01…", "sender": "Rina", "sender_id": "p_1c…", "identity": "contact",
                  "in_contacts": true, "text": "…", "at": "2026-11-22T19:02:11+07:00"}],
    "can_reply": true, "posted_at": "2026-11-22T19:02:11+07:00"
  }],
  "removed": [{"key": "n_8f3a…", "reason": "cleared"}],
  "all_removed": false
}
```

- Unknown fields are refused (`extra=forbid`). `conversation_identity` is `shortcut` or `title`, and `identity` is
  `contact` or `name`. At most 5 messages per notification, 50 posted and 200 removed per report.
- Messages written on the phone (the user's own reply, or hers through a reply button) carry `sender_id: "owner"`.
  They are never news and never wake her. Her "Now" shows the last message from someone else, with "answered from
  the phone since" when the conversation's latest message is the owner's.
- **Switching the Notifications sense off**: send `{"all_removed": true}` once.
- Send a `revision` that goes up on every update of a key: an older revision arriving late is ignored.

## Phone state (`PUT /api/phone/state`, `sense.phone`)

- `media`: `{"app", "title", "artist", "state": "playing" | "paused"}` or `null`.
- `navigation`: `{"next_step", "distance_text", "eta_text"}` (each may be null) or `null`.
- `messages_on`: the Messages switch, `true` / `false`. This is how the backend knows replies are paused.

## Actions (`phone_action` frame / push, unchanged envelope)

| `action` | `args` sent |
|---|---|
| `media_control` | `{"command": "play" \| "pause" \| "next" \| "previous"}` |
| `set_volume` | `{"level": 0-100}` |
| `set_alarm` | `{"time": "HH:MM", "label"?, "days"?: ["mon", …, "sun"]}` |
| `set_timer` | `{"seconds": 1-86400, "label"?}` |
| `dismiss_notification` | `{"key"}` |
| `set_ringer` | `{"mode": "normal" \| "vibrate" \| "silent"}` |
| `set_dnd` | `{"on": bool}` |
| `reply_notification` | `{"key", "conversation_id", "revision", "text"}` |
| `open_app` | `{"name"}` |
| `open_link` | `{"url"}` (http/https only) |
| `play_music` | `{"query"}` |
| `whatsapp_draft` | `{"to"?, "text"}` |

Optional args are left out, never sent as null.

- Results use the phase 7 statuses. `notified` is accepted only from `navigate_to`, `open_app`, `open_link`,
  `play_music` and `whatsapp_draft`.
- `data` is accepted only on `done`: `open_app` → `{"opened": "<label>"}`, `whatsapp_draft` →
  `{"target": "contact" | "picker"}`. Coordinate actions are unchanged. Any other `data` gets `422`.
- Reasons with their own wording: `no_notification_access`, `no_dnd_access`, `no_contacts_access`,
  `notification_gone`, `no_reply_button`, `no_media_session`, `not_clearable`, `app_not_found`, `ambiguous`, `switched_off`,
  `stale`, `replies_paused` (and the phase 7 ones).
- **Before sending**, the backend checks that a reply or dismiss names a notification still in its store, that a
  reply has `can_reply`, and that `messages_on` isn't false for replies and drafts. The phone still runs its own
  dispatch check.
- Waits: 10 s on a live socket; 20 s once it went by push.

## Chat lines

A late answer **replaces** the "sent, no answer yet" line. The `phone_activity` frame has the same `message_id` as
that line, plus `"replaces": true`. The phone updates the line it shows instead of adding one, and history has
one line.

## Which turns can do what

- Replies to the user: every action.
- Her own turns (wakes): `media_control`, `set_volume`, `set_alarm`, `set_timer`, `dismiss_notification`,
  `set_ringer`, `set_dnd`, plus the Maps lookups. `reply_notification` only on a wake bound to an exchange (affinity B3), paused by the Messages switch.
- A turn caused by someone else's notification gets no actions at all.
