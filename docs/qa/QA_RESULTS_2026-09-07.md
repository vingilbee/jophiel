# MacroVision QA execution report

Executed 2026-09-07 against isolated disposable PostgreSQL/Redis services and local QA servers. No personal accounts, Google mail, real external documents, or desktop contents were used. Synthetic AI outputs were used for deterministic transport tests; a separate live pass used the configured OpenAI model and local MacroVision weights with a generated plate image.

## Overall result

The app is **not ready for public GitHub publication or deployment** without fixing the P0 security/data issues below.

| Suite | Result |
|---|---:|
| Backend existing tests | **71 passed**, 2 dependency deprecation warnings |
| Frontend lint | Pass with 2 Fast Refresh warnings |
| Frontend production build | Pass |
| Existing mocked Playwright tests | **2 passed, 2 failed** |
| Real API/WS audit | **134 passed, 11 failed** |
| Real browser audit | **59 passed, 17 failed** (includes repeated mobile/selector cases) |
| Memory/prediction service audit | **8 passed, 8 failed** |
| Additional edge audit | **7 passed, 4 failed** |
| Live model/chat pass | **7 passed, 0 failed** |

The raw result files and screenshots are in [docs/qa/artifacts](/Users/vincentgilbert/Desktop/NutritionDetection/app/docs/qa/artifacts). Counts include intentionally repeated browser cases across Chromium, Firefox, WebKit and mobile sizes; they are evidence, not a statement of unique product requirements passed.

## P0 findings

### BUG-01 — Logout does not revoke the session cookie

**Severity:** P0 security. **Status:** reproduced.

`POST /api/auth/signout` returned 204, but replaying the old `qa_session` cookie still returned 200 from `/api/auth/me`. The implementation creates and resolves sessions in Redis but `delete_session()` deletes a PostgreSQL `UserSession` row instead. The old Redis key remains valid.

Reproduction: sign in, save cookie, sign out, call `/api/auth/me` with the saved cookie. Evidence: `api-results.json`, `AUTH-06 replay revoked cookie`.

**Fix follow-up (2026-09-07):** Logout now deletes the hashed session key from Redis, matching session creation and lookup, and no longer depends on a database session. Added `backend/tests/test_auth_sessions.py`: HTTP regression checks confirm old-cookie replay returns 401, another session remains valid, the response clears the cookie, and repeated/missing/unknown-session logout returns 204. These tests use isolated Redis/database substitutes; the original live QA audit has not been rerun. Backend suite: **74 passed**, 2 dependency deprecation warnings (`uv run --no-sync python -m pytest tests -q`). BUG-02 (already-open WebSocket revocation) is separate and unchanged.

### BUG-02 — An already-open WebSocket survives logout/session revocation

**Severity:** P0 security. **Status:** reproduced.

After logout, and even after explicitly deleting the Redis session key, the existing chat WebSocket accepted a new `run` and streamed a complete reply. Authentication is checked only before accepting the socket. New messages need a revocation check, or the server must close active sockets when the session is revoked.

Evidence: `AUTH-09 socket after logout` and `AUTH-09 socket after actual Redis revocation`.

**Fix follow-up (2026-09-07):** `chat_ws` now revalidates the cookie session before processing every incoming frame, on idle receive timeouts (one second), and before awareness heartbeats. Missing/expired sessions or validation failures close the socket with code 1008. Revocation cancels that socket's active reply; other sessions retain their own connections and turns. Ordinary transport disconnects retain the existing detached-generation behavior.

Added WebSocket regression coverage in `backend/tests/test_auth_sessions.py` for logout followed by text/audio frames, idle logout, direct Redis-session deletion with another session still connected, revocation during streaming, session-store failure, and reconnect with a revoked cookie. These use real FastAPI WebSocket handling with isolated Redis/database substitutes and synthetic model output; the original live QA audit has not been rerun. Full backend suite: **81 passed**, 2 dependency deprecation warnings (`uv run --no-sync python -m pytest tests -q`).

### BUG-03 — Hostile-origin state-changing requests are accepted

**Severity:** P0 security. **Status:** fixed in code; verified with isolated regression tests on 2026-09-08. Original live audit not rerun.

With a valid cookie and `Origin: https://hostile.example`, a multipart `POST /api/images` returned 201. Hostile CORS preflight was rejected, but direct browser form/fetch CSRF protection is not enforced at the API boundary. WebSocket connections from a hostile origin were also accepted.

Evidence: `edge-results.json` `SEC-02 hostile Origin mutation`, `api-results.json` `SEC-02 hostile-origin socket`.

**Fix follow-up (2026-09-08):** All 17 POST/PATCH/DELETE endpoints in the API routers now require `Depends(validate_origin)`, which exactly matches the Origin header against `settings.cors_origins` and returns 403 otherwise. Chat WebSockets check the same allowlist before authentication and acceptance. Hostile, missing, and literal `null` origins are rejected. Requests from scripts/test clients must now supply a trusted Origin; GET routes are unchanged.

Updated `backend/tests/test_auth_sessions.py` to send a trusted Origin for existing authentication tests. Added coverage confirming untrusted multipart uploads return 403 without calling image storage, untrusted WebSocket handshakes are rejected before database authentication, and all mutating HTTP routes carry the origin dependency. Existing logout/session-revocation checks pass with trusted origins. Full backend suite: **88 passed**, 2 dependency deprecation warnings (`uv run --no-sync python -m pytest tests -q --tb=short`). Tests use isolated Redis/database substitutes and synthetic model output; these results do not replace the original live/browser audit. The original hostile-origin upload test explicitly supplied a cookie, so browser exploitability of the original finding also depended on SameSite and deployment topology.

### BUG-04 — Account deletion leaves private uploaded files on disk

**Severity:** P0 privacy/data deletion. **Status:** fixed in code; regression-tested on 2026-09-08. Requires database migration and existing-worker restart; original live audit not rerun.

Deleting the user removed database rows and invalidated the current cookie, but left 28 image files and 30 asset files in the user’s storage directory. There is no cleanup in `delete_user()` for Redis keys or local image/asset directories.

Evidence: `api-results.json` `DATA-05 account file cleanup`; database cascade itself passed.

**Fix follow-up (2026-09-08):** Account deletion now inserts a durable `file_deletion_jobs` record in the same transaction that deletes the user and cascaded database records. It clears the cookie and returns **202 Accepted** with `{"status":"deletion_pending"}`. The existing memory-worker process also handles deletion jobs; no additional process is required. Cleanup removes user images/assets and their metadata, then their Redis session records, with exponential retries for failures. Authentication rejects the removed user immediately. Per-user filesystem locks and empty deletion markers prevent in-flight writes from recreating removed upload directories; tiny control files and completed job metadata remain.

Verified using temporary storage, substituted Redis, and a disposable PostgreSQL instance: durable scheduling, transaction rollback, partial-failure retries, cross-user isolation, late-write rejection, concurrent claim exclusion/recovery, shared-worker dispatch, and HTTP acceptance with immediate old-cookie rejection. Full backend suite: **97 passed**, 2 dependency deprecation warnings. All migrations, including `9d72a4f831c0`, applied cleanly to the disposable database. The personal application database was not migrated, and historical orphan files were not purged. See [ACCOUNT_DELETION.md](../ACCOUNT_DELETION.md) for setup, operations, retained control metadata, and test instructions.

**Concurrent-worker follow-up (2026-09-08):** `python -m app.workers` now runs independent memory and deletion threads in one process, each with its own database sessions and Redis wake-up client. Deletion no longer waits behind a busy memory job. Added tests for progress while either queue is blocked, shutdown draining, callback retry, and separate queue waits. Latest backend run: **97 passed, 5 skipped**, 2 dependency deprecation warnings; the five PostgreSQL-only cleanup tests were not rerun for this scheduling change. Their earlier disposable-database results above remain the database validation evidence. Restart the existing mprocs worker entry to activate the new runner.

### BUG-05 — Raw session token is printed during sign-in

**Severity:** P0 credential exposure. **Status:** reproduced statically and captured in the redacted QA log.

`backend/app/api/routes/auth.py` contains `print("USER: ", user, token)`. The QA harness redacted the value before writing evidence; the production code would expose the token to logs. Remove it and scan logs/history before publication.

Evidence: `api-results.json` `AUTH-08`; source line in `backend/app/api/routes/auth.py`.

## P1 findings

### BUG-06 — Signup navigates to chat without creating a session

**Status:** fixed in code; authentication regression tests passed on 2026-09-08. Original browser reproduction was on Chromium, Firefox, WebKit, and mobile; browser audit not rerun.

The API signup response is 201 with no session cookie, while `useAuth.signup()` stores the returned user and navigates to `/chat`. `/api/auth/me` immediately returned 401; a refresh returned to `/login`.

Fix either signup to set a session cookie or make the UI sign in before navigating.

**Fix follow-up (2026-09-08):** Signup now creates a Redis session for the newly created user and sets the session cookie before returning 201. Added tests starting without cookies, signing up, and authenticating `/api/auth/me` from a fresh HTTP client using only the returned cookie; the user ID matches the new account. Logout revokes the new session, and duplicate signup returns 409 without creating a session or setting a cookie. Authentication suite: **19 passed**, 2 dependency deprecation warnings. User creation, database lookup, and Redis storage are substituted in these tests; this is HTTP regression evidence rather than a live browser rerun.

### BUG-07 — Tracking PATCH accepts null and fails with HTTP 500

**Status:** fixed in code; regression-tested on 2026-09-08. Original live audit not rerun.

`PATCH /api/dashboard/weight-logs/{id}` with `{"date": null}` or `{"weight": null}` and the equivalent calorie payload returned 500 instead of 422. The update schemas allow nullable fields, then the database rejects them. Update schemas now reject explicit `null` values while still allowing omitted fields for partial updates.

### BUG-08 — Non-finite calorie values are accepted

**Status:** fixed in code; regression-tested on 2026-09-08. Original live audit not rerun.

`{"calories": 1e309, ...}` was accepted with 201. Add finite-number validation to all numeric schemas and reject non-finite JSON values before persistence/serialization. Weight and calorie schemas now reject non-finite values; prediction responses and regressor outputs are sanitized to finite nonnegative numbers.

### BUG-09 — Malformed VLM output can crash Combined prediction or return invalid macros

**Status:** fixed in code; regression-tested on 2026-09-08. Original live audit not rerun.

Missing VLM fields raised `KeyError`; wrong numeric types raised `TypeError`; negative values and `NaN` propagated through `NutritionPrediction`. VLM JSON is now validated before arithmetic; malformed output returns `None`, causing a finite regression fallback. Optional depth/context is omitted safely when absent.

### BUG-10 — Local Qwen VLM dispatch has a signature mismatch

**Status:** fixed by removing the local dispatch; regression-tested on 2026-09-08. Original live audit not rerun.

`_vlm_estimate()` previously called `_qwen_estimate(img, hints)`, but `_qwen_estimate()` accepted only `img`. OpenAI is now the only supported VLM backend; Qwen is not imported or dispatched by the normal predictor path.

### BUG-11 — EXIF orientation is not normalized

**Status:** fixed in code; regression-tested on 2026-09-08. Original live audit not rerun.

A JPEG marked orientation 6 decoded at `(80, 40)` rather than the expected portrait orientation `(40, 80)`. Use `ImageOps.exif_transpose()` before model input and storage, then verify the saved/displayed image.

### BUG-12 — Memory worker can process one job twice after a concurrent claim

**Status:** fixed in code; regression-tested on 2026-09-08. Original live audit not rerun.

Two concurrent workers both called extraction and both returned success for the same job. Jobs stuck in `processing` are also not returned by `next_pending_job_id()`, so a crash after claiming can leave them stranded. Add an atomic claim with ownership/lease and reclaim timeout, and ensure only the claimant commits completion.

**Fix follow-up (2026-09-08):** Memory jobs now claim only pending rows or processing rows whose lease has expired, using `FOR UPDATE SKIP LOCKED`. Each claim records a random owner token and 15-minute lease, commits ownership before model work, and requires that token for completion/failure updates. Concurrent workers skip a live claim; a worker crash leaves an expired claim reclaimable rather than stranded. Apply migration `af93c7d1e2f4` before starting workers. Full backend suite: **106 passed, 5 skipped**, 2 dependency deprecation warnings; live multi-process recovery has not been rerun.

### BUG-13 — Mobile header overflows horizontally

**Status:** fixed in code; lint/build passed on 2026-09-08. Original browser audit not rerun.

The header measured 693px wide on a 390px viewport. Integrations, Cognitive test, New chat, and Sign out render offscreen. The cognitive inspector also causes the underlying chat layout to extend beyond the viewport. Collapse or scroll the navigation/account controls intentionally at mobile breakpoints.

The shared header now keeps the logo and account/actions in a compact top row and puts navigation in a full-width horizontally scrollable row on narrow viewports. Actions wrap within the available width.

### BUG-14 — Login inputs have no programmatic labels

**Status:** fixed in code; lint/build passed on 2026-09-08. Original browser audit not rerun.

The visible “Email” and “Password” text is not associated with the inputs (`labels: 0`, no `aria-label`/`aria-labelledby`). Use matching `htmlFor`/`id` values or equivalent accessible names.

The login form now associates Name, Email, and Password labels with stable input IDs using `htmlFor`/`id`.

### BUG-15 — Voice disabled state remains visually/semantically enabled

**Status:** fixed in code; frontend/backend checks passed on 2026-09-08. Original browser audit not rerun.

With the server voice feature disabled, both “Start voice” buttons were enabled. Clicking can still attempt microphone setup and the UI does not communicate that the capability is unavailable. Gate the UI from the server capability/configuration and ensure both orb and button are disabled or replaced with an unavailable state.

The backend now exposes `/api/capabilities` with the configured `voice_enabled` flag. The dashboard fetches it before enabling voice and disables both the orb and primary action when the server reports voice unavailable, showing “Voice unavailable” and avoiding microphone setup. `bun run lint` (two existing Fast Refresh warnings), `bun run build`, and the backend suite (**106 passed, 5 skipped**) passed. The original browser audit was not rerun.

### BUG-16 — Existing interruption Playwright tests fail at the first streamed token

**Status:** fixed in test fixture; verified on 2026-09-08 with one Chromium worker.

Both `frontend/e2e/chat-interruption.spec.ts` tests time out before rendering `First partial answer`/`Still generating`; the document preview tests pass. These tests use mocked WebSockets, so the failure is in the browser/socket test contract or client timing, not proof that the real backend stream is broken. Update the fixture/assertion to wait for socket readiness or diagnose the mock route lifecycle.

The fixture now explicitly waits for the intercepted WebSocket route before submitting the first turn. Both interruption tests pass: **2 passed (5.7s)**. Frontend lint also passes with the two existing Fast Refresh warnings. This verifies the mocked contract; real browser streaming was already covered separately above.

### BUG-17 — Pixel-bomb image input returns 500

**Status:** fixed in code; regression-tested on 2026-09-08. Original live audit not rerun.

A PNG with a 20,000×20,000 declared dimension produced 500 from both `/api/images` and `/api/predict`. Catch Pillow decompression-bomb/image-size exceptions and return a bounded 400. Also enforce ingress body and pixel limits before reading large uploads.

`image_store.decode_image()` now checks declared dimensions before loading pixels, with a configurable `MACROVISION_MAX_IMAGE_PIXELS` limit (default 40 million), and converts Pillow decompression-bomb warnings/errors into `InvalidImageError`. Both routes already map that error to HTTP 400. Added a regression test proving `img.load()` is never called for an oversized declaration. Focused image tests: **24 passed**; full backend suite: **107 passed, 5 skipped**, 2 dependency deprecation warnings. Original live pixel-bomb audit was not rerun.

## Positive results

- All 71 existing backend tests pass when invoked as `python -m pytest tests -q`. Running bare `pytest tests -q` from this environment failed collection because the current import path did not include `backend`; use the module invocation or configure test paths.
- Frontend production build passes. Lint has only two `react(only-export-components)` Fast Refresh warnings.
- Real browser flows for sign-in after manual signin, chat streaming, stop, conversation refresh, cognitive rules inspection, prediction upload/result, invalid image UI, integration page and document preview worked on Chromium, Firefox and WebKit desktop.
- Real API checks confirmed user scoping for logs/images/assets/conversations, foreign attachment IDs are discarded, screen-frame retention is capped at 12 and hidden from the default image list, migrations apply cleanly, and database cascades remove account rows.
- Live synthetic chat streamed a real OpenAI response with first token around 2.74s and total completion around 2.91s. Live regression returned finite nonnegative output in about 8.32s cold/2.08s warm; Combined returned finite output in about 3.80s. These are technical smoke results only, not accuracy evidence.
- Thirty concurrent ordinary image-list reads completed without errors (local exploratory p95 approximately 121ms).

## Important limitations

Microphone, speaker, camera, browser screen-share permissions, Google OAuth, real Gmail mutations, Blender desktop control, deployment HTTPS/proxy behavior, backup restore, and weighed-meal nutrition accuracy were not claimed as completed. The separate test plan specifies those checks in [END_TO_END_TEST_PLAN.md](/Users/vincentgilbert/Desktop/NutritionDetection/app/docs/END_TO_END_TEST_PLAN.md).

The QA harness and synthetic artifacts are local diagnostics. Do not publish `docs/qa/artifacts` if it contains environment-specific traces or generated outputs; review the publication manifest first.

## Recommended order before release

1. Fix BUG-01 through BUG-05, then rerun authentication, deletion, CSRF/origin, log-scan and cross-user tests.
2. Fix signup/session behavior, numeric validation, VLM validation/Qwen dispatch, EXIF handling, memory job leasing and pixel-bomb handling.
3. Fix mobile header/accessibility/voice capability UI and repair interruption tests.
4. Run a clean production build behind the intended HTTPS reverse proxy with secure cookies and real WebSocket upgrades.
5. Review all files, history, Docker layers, environment examples, model licenses and generated artifacts before any GitHub push.
