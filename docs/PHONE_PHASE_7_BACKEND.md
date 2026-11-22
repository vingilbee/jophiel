# Phase 7 phone actions and Maps

The backend sends an action to the most recently active connected phone, or to the most recently seen signed-in phone through FCM. The selected bearer session owns the action. A command expires after 60 seconds. The reply waits up to 10 seconds for navigation or a saved place, and 15 seconds for the current position. The first valid result wins. If a socket has already received the action, the backend will wait for its result without attempting push delivery. An outcome that arrives after the tool stopped waiting becomes news for the phone mind. The server accepts results for up to 60 seconds after command expiry so a delayed report can still reach the conversation.

## Phone contract

A WebSocket action looks like this. FCM sends the same fields with `args` encoded as a JSON string.

```json
{
  "type": "phone_action",
  "action_id": "7f79dbc9-ecc3-4f50-bb67-de051b50dfd4",
  "action": "navigate_to",
  "args": {
    "name": "Kemang Village",
    "lat": -6.26,
    "lng": 106.81,
    "query": null,
    "mode": "drive",
    "place_id": "optional Google place ID"
  },
  "expires_at": "2026-11-22T02:00:00+00:00"
}
```

| Action | Arguments | Successful result |
| --- | --- | --- |
| `navigate_to` | `name`, `lat` and `lng` when resolved, `query` for a text fallback, `mode`, optional `place_id` | `done` when Maps opens, or `notified` when the notification is shown |
| `get_position` | `{}` | `done` with `data: {lat, lng, accuracy_m, fix_at}` |
| `resolve_saved_place` | `{"name":"work"}` | `done` with `data: {lat, lng}` from the phone's saved-place settings |

For `resolve_saved_place`, the phone can use a trimmed, case-insensitive name match. Return `failed` with `not_found` or `ambiguous` when there is no unique match. Respect the phone action and Places switches. Resolving a saved place uses its stored centre and requires no live position fix. Older phone builds that return `unknown_action` still work with entered addresses and current-position requests.

Results use the `phone_action_result` socket frame with `action_id`, `status`, optional `reason`, and optional `data`. The same phone can use `POST /api/phone/actions/{action_id}/result` with bearer auth and a body containing `status`, `reason`, and `data`. The full socket frame is also accepted as a POST body. The endpoint returns HTTP 204 for accepted results and repeats, 404 for unknown actions or a different session, 422 for malformed results, and 503 for temporary server errors.

Phone chat receives `{type: "phone_activity", conversation_id, message_id, text, created_at}`. WebSocket history and the phone's REST conversation response include the corresponding message as `role: "assistant"`, `kind: "phone_activity"`, `source_channel: "system"`, and `content` set to the line's text. Deduplicate by the history `id` or live `message_id`.

Settings can make a bearer-authenticated `GET /api/phone/places/search?q=...` request. The response is an array of `{name, address, lat, lng}` objects using the basic Places fields.

Chat tools can reuse a known Google Maps place ID across replies. Use `destination_place_id` for navigation or routes and `origin_place_id` for a route's starting point. Navigation resolves an uncached ID through Place Details (New).

## Deploying

1. Apply the Alembic migration from the backend directory with `alembic upgrade head`. The API and chat/mind workers use the new `phone_activity` table.
2. Enable billing, Routes API, Places API (New), and Geocoding API in a Google Cloud project. Create an API key restricted to those services. Set `GOOGLE_MAPS_API_KEY` in the backend API and chat worker environments. Redis connects the API and workers, and the API and chat worker need the same FCM service-account configuration for push delivery. [Google API key setup](https://developers.google.com/maps/documentation/places/web-service/get-api-key)
3. Check the current SKU free usage caps before setting Google Cloud quotas. A conservative starting point is **30 Text Search calls/day**, **30 Compute Routes calls/day** if two-wheeler requests share that quota, and **300 Geocoding calls/day**. The listed monthly free caps are 1,000 for Text Search Enterprise and Compute Routes Enterprise, 5,000 for Compute Routes Pro, and 10,000 for Geocoding. If the Console offers independent quota controls, about **160/day** applies to driving traffic-aware routes alone. If a daily quota is unavailable, review the method's available limits and monitor the corresponding SKU. [Current pricing](https://developers.google.com/maps/billing-and-pricing/pricing) · [Quota controls](https://developers.google.com/maps/billing-and-pricing/manage-costs)
4. Configure a billing alert, for example at US$1. Billing alerts send notifications, while quotas limit requests. When Google rejects a lookup at a quota, chat and Settings report that Maps is unavailable. [Google Cloud cost controls](https://developers.google.com/maps/billing-and-pricing/manage-costs)

Live-position replies travel over ephemeral encrypted Pub/Sub and stay in the waiting backend process until the lookup finishes. History stores only the activity text. Two-wheeler route estimates fall back to driving if Google can't route that mode. Navigation checks Google's published coverage using the destination country and falls back to driving when coverage can't be verified. [Two-wheeler coverage](https://developers.google.com/maps/documentation/routes/coverage-two-wheeled)
