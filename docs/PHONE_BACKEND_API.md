# Phone app: what the backend provides (phases 1–3)

The backend half of `frontend-phone/PLAN_PHASE_0-3.md`, branch `feature/phone-backend`.
Apply the migrations first: `cd backend && uv run alembic upgrade head` (`a1f4c7e2d9b3`, `b2e5d8f1a4c6`).

## Auth

| Call | What |
|---|---|
| `POST /api/auth/token` `{email, password, device_name}` | → `{token, user, expires_at}`. No cookie, no Origin needed. 5/minute. Wrong password → 401 `"Incorrect email or password"`; too many → 429. |
| Every REST call and the WebSocket | `Authorization: Bearer <token>`. When the header is sent, it is the only credential looked at. |
| `POST /api/auth/signout` (bearer) | Ends this session and deletes this phone's device row (no more pushes). 204. |
| Session life | 90 days, sliding: any authenticated request renews it once less than half is left. |
| Expired / revoked | REST → 401; WebSocket → close code 1008 (at the handshake or within ~1 s of revocation). |

## Chat WebSocket `/api/chat/ws`

Protocol: the header of `backend/app/api/routes/chat.py`. What's new for the phone:

- **`run`**: send `turn_id` (a UUID the phone makes, one per queued message) and `written_at` (ISO time,
  the phone's clock at send). Resending the same `turn_id` (even on a new socket) returns the original
  `ack` (and `seen`, if she already picked it up) and saves nothing new. Note: like the web app, a new `run`
  while she's still replying stops that reply.
- **`ack`** `{turn_id, user_message_id, conversation_id}`: saved → ✓.
- **`seen`** `{message_id, conversation_id}`: her chat worker picked it up → ✓✓. Sent to every socket of the
  user. `message_id` is the `user_message_id` from `ack`.
- **`history`** messages now carry `written_at`, `seen_at` and `source_channel` (`"phone"` for messages sent
  from the phone). REST `GET /api/chat/conversations/{id}` has the same fields.
- **`sense`**: send `{"type": "sense", "client": "phone", "visible": true|false, "timezone": "..."}` on every
  app-state change. `visible: true` means the app is open on screen (then no push is sent). The server takes
  "phone" from the session, not the frame.
- `written_at` more than ~2 minutes before arrival shows in her history as "written N hours before it arrived".

## Push (FCM, data-only)

Backend config: `FCM_SERVICE_ACCOUNT_FILE=/path/to/service-account.json` (and optionally `FCM_PROJECT_ID`).
Without it nothing is sent; `GET /api/capabilities` → `push_enabled: false`.

| Call (bearer, phone sessions only; a web cookie → 403) | What |
|---|---|
| `PUT /api/phone/device` `{fcm_token, device_name?}` | Register / refresh after login and whenever the FCM token changes. |
| `PATCH /api/phone/device` `{notifications_enabled?, show_text?}` | Settings switches. |
| `GET /api/phone/device` | Current switches (404 before registering). |
| `DELETE /api/phone/device` | Stop pushes for this session (signout does this too). |

Data payloads (all values strings):

- `{"type": "message", "conversation_id", "message_id", "text"?}`: `text` only when `show_text` is on
  (trimmed to 200 chars). `message_id` is her message's id (scroll target).
- `{"type": "clear", "conversation_id"?}`: the user is back at an app; cancel all Jophiel notifications.

A message is pushed ~3 s after it's saved, unless at that moment: the phone app is visible, a web app is
visible and was used in the last 2 minutes, or she said it aloud on a live voice session.

## Settings → About

`GET /api/capabilities` (no auth) → `{voice_enabled, desktop_enabled, push_enabled, backend_version}`.
`GET /health` → `{"status": "ok"}`.
