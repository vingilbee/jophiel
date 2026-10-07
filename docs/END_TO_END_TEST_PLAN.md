# MacroVision / Columbina — pre-release test plan

> Scope clarification (2026-10-05): This is a historical pre-release inventory of implemented surfaces. Columbina's current product focus is a general conversational agent with identity and personality. The prediction and tracking cases below cover specialist capabilities. Use [the conversational test plan](COLUMBINA_CONVERSATIONAL_TEST_PLAN.md) for primary conversational quality evaluation.

Prepared: 2026-09-07. Baseline: source files in this workspace; no Git revision available (`git status` reports this directory is not a repository).

**Execution status: NOT RUN.** This document is the requested first step: application discovery and detailed test cases. Reading source and finding existing tests does not establish that the app works. No production deployment, GitHub publication, external messages, or account mutations were performed in preparing this plan.

## 1. What the application currently is

MacroVision estimates calories, protein, carbohydrates, and fat from food photos. Columbina adds a personal assistant with persistent streaming conversations, food-analysis delegation, voice, screen awareness, proactive messages, durable memory, Google Workspace tools, generated document previews/downloads, and an optional Blender bridge.

| Surface | Actual implementation / test boundary |
|---|---|
| `/`, `/login` | React landing page and combined signup/signin form. |
| `/predict` | Authenticated image picker, drag/drop or mobile camera; Combined/Fast modes, preview, reset, estimates. No save-to-calorie-log control found. |
| `/chat` | Authenticated WebSocket chat, conversation REST history, image/assets, interruption, document rendering, cognitive inspector. |
| `/dashboard` | Voice interface with orb, transcript, microphone and screen controls. It is **not currently a weight/calorie chart dashboard**. |
| `/integrations` | Registry-driven connection cards and Google OAuth result handling. |
| Backend | FastAPI, SQLAlchemy/PostgreSQL, Redis-backed session lookup, local image/asset storage, mounted nutrition MCP service. |
| Tracking API | Weight and calorie CRUD under `/api/dashboard`; test directly until a tracking UI exists. |
| AI | Cached nutrition predictor, optional local Qwen or remote VLM, streaming orchestrator and specialist tools. |
| Background work | Separate memory worker; PostgreSQL durable jobs and optional Redis wake-up. Redis is also required by the current authentication implementation. |
| Optional services | GPT-SoVITS sidecar, Google OAuth/API access, Blender desktop bridge and model artifacts. |

Source anchors: `frontend/src/App.tsx`, `frontend/src/pages/*`, `frontend/src/hooks/*`, `frontend/src/api/*`, `backend/app/main.py`, `backend/app/api/routes/*`, `backend/app/schemas/*`, `backend/app/core/config.py`, `backend/app/services/*`, `backend/app/ai/subagents/registry.py`, `docker-compose.yml`, `backend/Dockerfile`.

`CLAUDE.md` provides useful history but has stale statements: SQL sessions versus Redis implementation, prediction storage, dashboard purpose, and a rules-only inspector versus the current rules/LLM modes. Tests below follow executable code and identify desired release behavior separately.

## 2. Findings to reproduce first

These are static findings, not executed test results. Preserve evidence and reproduce before assigning runtime impact.

| Finding | Evidence | Required case / release implication |
|---|---|---|
| Raw session token printed on successful signin | `backend/app/api/routes/auth.py`: `print("USER: ", user, token)` | AUTH-08. Credential disclosure in logs; P0 release blocker. Never copy actual tokens into reports. |
| Signout uses a different session store | `services/auth.py`: `create_session`/`get_user_for_token` use Redis; `delete_session` deletes a `UserSession` SQL row | AUTH-06. Replay the old cookie after logout; suspected session-revocation failure. |
| Signup may appear authenticated without a session | Signup API creates a user without setting a cookie; `useAuth.signup` sets returned user and login page navigates to `/chat` | AUTH-02. Verify protected API, socket, and refresh immediately after signup. |
| Account deletion only deletes database user in auth service | `delete_user` commits a DB deletion; no filesystem/Redis cleanup there | AUTH-10, DATA-05. Verify retained photos, assets, session keys and running jobs. |
| Uploads are fully read before application validation | Prediction/image/asset handlers call `await file.read()` | SEC-05. Verify ingress request limits and memory behavior, not only post-read rejection. |
| Container copy can include private files and weights | Dockerfile uses `COPY . .`; backend `.dockerignore` does not exclude `var/` or model weights | REL-03. Inspect an isolated build context and resulting image. |
| Root publication guard is absent | No root `.gitignore`; backend ignores `.env.example`; sidecar/vendor files exist | REL-01/02. Review intended publication manifest before initializing/staging. |
| Compose is incomplete for serving the whole app | Defines DB, migrations, Redis, memory worker; no frontend/API/TTS serving service | REL-04. A successful Compose start is not an application deployment. |
| Production API routing is unspecified | Frontend uses relative `/api`; Vite dev proxy handles HTTP and WS | REL-05. Static production hosting needs explicit API and WebSocket routing. |
| Liveness does not establish dependency health | `/health` returns a constant status | REL-07. Verify dependency readiness separately. |
| WebSocket authentication appears connection-time only | `chat_ws` resolves cookie before accepting; no Origin check found in that handler | SEC-02 and AUTH-09. Test hostile origins and revocation during an open socket. |

## 3. How to execute and record tests

### Priorities and test layers

- **P0:** account/data isolation, credentials, primary authentication, unauthorized external actions, unrecoverable data loss. Must pass before any public deployment.
- **P1:** advertised core behavior, error recovery, production build/routing, prediction integrity. Must pass for enabled release features.
- **P2:** secondary usability, compatibility, diagnostics, longer resilience checks. Track unresolved issues explicitly.
- **B:** real browser → actual API/WebSocket → real PostgreSQL/Redis/storage. No `page.route`/WebSocket mocks for claims of complete E2E coverage.
- **A:** direct API/WebSocket integration against isolated real persistence, asserting HTTP/frame contracts and stored effects.
- **D:** deterministic unit/service tests with controlled providers, clocks, failure injection and temporary storage.
- **L:** live model/provider/worker integration. Record model/config versions; assertions concern behavior and evidence, not exact prose.
- **M:** manual hardware/browser or operational checks.

All cases below start **NOT RUN**. Every row provides its setup/input, ordered actions, and pass criteria. Shared prerequisites and evidence requirements apply to every row. Expand every listed boundary/format/origin/browser into an individual run result; do not report an entire parameterized row as passing after one sample.

### Isolated fixtures and prerequisites

1. Use a disposable PostgreSQL database and Redis instance with unmistakable test names, temporary image/asset directories, separate app ports, and a dedicated test configuration. Do not point tests at the existing user's data or use `FLUSHALL` on shared Redis.
2. Seed A and B with distinct synthetic emails such as `qa-a-<run>@example.com` and `qa-b-<run>@example.com`; keep C unregistered. Use distinct browser contexts and cookie jars. Never put passwords/cookies/keys in committed fixtures or evidence.
3. Capture runtime versions (Bun, uv, Python, OS, browser), configuration **names and nonsecret values**, lockfile hashes, model checkpoint hashes, feature switches, CPU/GPU/RAM, proxy topology and migration head.
4. Apply all migrations to the disposable DB. Start Redis, backend and frontend explicitly. Start the memory worker/TTS only for corresponding phases. Disable nudges and remote providers during deterministic suites unless the case tests them.
5. Prepare known small RGB JPEG and PNG food images, EXIF-rotated phone photo, transparent/grayscale image, non-food scene, empty/truncated bytes, MIME-spoofed file, very large pixel image, and files just below/at/above configured byte limits. Generate stress fixtures in temporary storage.
6. Prepare valid PDF/DOCX/PPTX/XLSX/TXT/CSV/PNG/JPEG/BLEND/FBX/OBJ/GLB/GLTF assets, an invalid PDF, unsupported extension, path-like filenames, Unicode names and upper-case extensions. Real parsers should validate exported artifacts; a `%PDF` header alone does not prove a valid document.
7. Seed weight/calorie rows for both users on 2026-09-06, 2026-09-07 and 2026-09-08; conversations with 0, 1, 24, 25 and 100 messages; isolated screen frames, durable facts and pending memory jobs.
8. Use deterministic provider substitutes for rate limits, invalid JSON, partial streams and failed tool calls. Run a separate small live-provider pass afterward; a mocked provider pass does not establish actual provider compatibility or model quality.
9. For live Google tests use dedicated test accounts/files and an isolated folder. Sending mail, sharing, or permanently deleting real remote resources requires separate explicit authorization; until then exercise those paths with provider substitutes. Do not send messages as part of this planning task.
10. For Blender use a disposable scene and output folder; for screen capture share a synthetic test window. Never use private desktop contents as test evidence.

### Result record, evidence and cleanup

For each execution create a record with: case ID and parameter, status (`PASS`, `FAIL`, `BLOCKED`, `NOT RUN`, or justified `N/A`), date/tester, build/config identity, fixture IDs, exact steps, expected/actual result, duration, redacted HTTP or WebSocket evidence, relevant DB/filesystem assertions, screenshot/trace path, defect ID and cleanup outcome. A missing credential/device is **BLOCKED**, not PASS. A disabled optional feature can be N/A only when its UI/API are verified unavailable and the release scope records that decision.

Capture browser console errors and failed requests, ordered WebSocket frame types, status/headers, row counts and owner IDs, file existence, worker status and timing. Keep private traces local; redact cookies, bearer links, OAuth codes, personal content and provider payloads. Hash or summarize credentials rather than displaying them. Cleanup only resources created by the run; verify absence of test rows, files, worker jobs and external fixtures. Preserve failed-run artifacts privately before cleanup.

## 4. Authentication and navigation

| ID / priority / layer | Setup and steps | Expected result and persistence checks |
|---|---|---|
| AUTH-01 P1 B | Fresh context; open `/`, `/login`, then each protected route directly; try unknown path and browser back/forward. | Public routes render; protected routes redirect safely after auth resolution without exposing previous user content; unknown route returns home; no redirect loop. |
| AUTH-02 P0 B+A | Register C through UI; inspect signup response/cookies; send chat, call `/api/auth/me`, then refresh. Repeat direct signup API. | API signup returns 201 public user. UI must either obtain a real session before entering protected app or require signin explicitly; no false authenticated state. DB contains one user with password hash, no plaintext. Likely fails current UI flow. |
| AUTH-03 P1 B+A | Register with name lengths 0/1/255/256; password lengths 7/8/128/129; invalid email, whitespace-only name, Unicode name, missing fields. Bypass browser validation with API calls. | Defined schema boundaries return 201 or 422 appropriately; useful UI error and no invalid rows. Whitespace-only identity acceptance is documented as a validation defect if allowed. |
| AUTH-04 P1 A | Register A; repeat with same/lower/upper-case email. Submit two simultaneous signups for a new identical email. | Duplicate returns 409; exactly one normalized email row; race does not become unhandled 500. |
| AUTH-05 P0 B+A | Sign in with correct, wrong and unknown credentials; test mixed-case email; inspect cookie attributes and refresh. | Correct returns 200 and usable opaque session; wrong/unknown return 401 with same generic message. Redis key uses token hash and TTL; response excludes password hash; HTTPS cookie is HttpOnly/Secure with intended SameSite/path. |
| AUTH-06 P0 A+B | Save A's test cookie privately; sign out; replay old cookie against `/me`, prediction and new WS connection. Repeat signout without cookie. | Signout 204, cookie cleared, server credential revoked; old token cannot authenticate. Repeated signout is safe. Inspect Redis key deletion. Suspected failure in current code. |
| AUTH-07 P1 A | Set short session TTL; sign in, wait past expiry with controlled clock/short bounded wait; reuse expired/random/malformed cookie. | Protected requests reject with 401, expired Redis key gone; UI recovers to signin with no stale private data. |
| AUTH-08 P0 A | Capture isolated backend logs while successful/failed signin, OAuth failure and chat/provider failure occur. | No raw session token, password, OAuth secret/code, provider key or private request payload appears. Current signin print is a known static defect. |
| AUTH-09 P0 B+A | Open A chat socket in two tabs; sign out in one; attempt new turns on existing sockets and REST in other tab. Repeat account deletion/TTL expiry. | Revoked authentication cannot continue private reads, generation or tool calls on an established socket. UI notices invalidation; document current behavior if connection-time auth permits continued access. |
| AUTH-10 P0 A | Create A logs, conversation, images/assets, OAuth test grant, memory jobs and two sessions; DELETE `/api/auth/me`; replay all access paths. | 204, cookie cleared, DB cascades correct, all A credentials unusable, local/private data and pending jobs follow explicit deletion policy; B remains intact. API exists even though no account-deletion UI was identified. |
| AUTH-11 P1 B+A | Stop isolated Redis before signin and `/me`; restore it; repeat with missing `REDIS_URL`. Stop PostgreSQL separately. | No misleading successful login or credential leakage; understandable service error and recoverability; verify startup configuration catches required missing dependency or record gap. |
| AUTH-12 P1 B | Rapid double-submit signup/signin; navigate away during request; deny/offline network then retry; switch signup/signin modes after validation errors. | Buttons prevent accidental duplicates, errors clear appropriately, fields remain usable, no stale completion navigates unexpectedly or sets wrong user. |

## 5. Nutrition prediction and image storage

| ID / priority / layer | Setup and steps | Expected result and persistence checks |
|---|---|---|
| IMG-01 P1 B+L | As A, choose known JPEG on `/predict`, inspect preview, select Fast, Analyze; compare network JSON with displayed values. | One multipart POST `/api/predict?mode=regression`; finite macros, kcal/g labels, rounded display matches response; source is truthful. Stored image belongs to A; no calorie-log row auto-created. |
| IMG-02 P1 B+L | Repeat with Combined and configured VLM; inspect dish, portion scale and source; repeat same fixture in Fast. | Valid combined response renders dish/fallback title and optional portion multiplier; mode changes map correctly. Do not require combined to outperform regression without measured evidence. |
| IMG-03 P1 D+B | Make VLM timeout, return malformed/missing nutrition JSON or be disabled. Analyze in Combined. | Documented regression fallback produces truthful `source`, valid numbers and usable UI; no fabricated successful VLM result. Model-level failure is distinguished from VLM fallback. |
| IMG-04 P1 B | Drag/drop JPEG/PNG; cancel chooser; remove image; select same file again; try multiple dropped files. | Preview and selected file agree, reset removes stale result/error, same file can be selected again, first-file behavior is consistent, no accidental browser navigation. |
| IMG-05 P1 B+A | Submit empty, corrupt, truncated, nonimage and spoofed-MIME input to both `/predict` and `/images`; omit multipart field. | Invalid decoded image returns 400; missing required field 422; no model inference or invalid stored image; UI shows useful error. |
| IMG-06 P1 B+A | Test limit−1/limit/limit+1 bytes against configured upload cap, then change backend cap from default 10 MiB. | Default boundary behavior agrees in UI/API; oversized application rejection is 400; config mismatch with hardcoded frontend 10 MB is recorded, not silently ignored. |
| IMG-07 P1 A+D | Test EXIF rotation, grayscale/RGBA, odd aspect ratio, huge dimensions, animation and phone HEIC. | Supported inputs decode safely with correct orientation; unsupported formats fail cleanly; no uncontrolled memory use/decompression crash. Verify actual support rather than assuming all `image/*` works. |
| IMG-08 P1 B+D | Delay inference; double click Analyze, try Remove/mode change, navigate away; inject 500 and dropped response then retry. | One active submission; controls reflect loading; no stale results for wrong file; failure leaves retry usable and does not claim success. |
| IMG-09 P1 A | Upload with `/api/images`, list default and kinds/limit; fetch raw; delete; fetch again. Test limit 0, negative and above configured maximum. | POST 201; list is user-scoped and clamped, invalid minimum gives 422; raw has correct media type/private cache policy; delete 204 then 404. |
| IMG-10 P0 A | B attempts A image raw/delete and forged/path-like IDs; A supplies B image in chat/prediction followup and screen sense. | Foreign files indistinguishable from missing; no byte/metadata leakage; unauthorized attachment excluded and never used by a tool. B's files remain unchanged. |
| IMG-11 P1 D+A | Make image storage unwritable/full while valid prediction can run; then fail inference after image save. | Storage OSError fallback still returns valid prediction with null image ID; UI tolerates it. Record and bound orphaned uploads when inference fails. No request writes outside configured root. |
| IMG-12 P1 L | Repeat regression cold/warm predictions; send concurrent requests while polling `/health` and chatting. | Predictor reuse verified; no repeated checkpoint load per request, cross-request state or unbounded GPU/CPU exhaustion; latency captured separately for cold/warm and device. |
| IMG-13 P1 L | Use non-food, blurred/occluded meals, different angles, extra oil/sauce and doubled portions; record actual model outputs. | No crashes/nonfinite values; estimates described as estimates, uncertainty/failure not fabricated away. Cases assess robustness; numeric plausibility alone is not accuracy validation. |
| IMG-14 P1 B+L | Predict as A; use returned image in chat and ask for portion/depth explanation; refresh history. | Specialist uses exact authorized image; values are traceable to tool output; attachment persists and depth output stays private. |
| IMG-15 P1 L+M | Execute `REAL_WORLD_NUTRITION_VALIDATION.md`: 30-meal pilot then locked new 100+ meal cohort, weighed preparation records, device/cuisine/lighting tags. | Report per-macro MAE, bias and error distribution by mode/subgroup; identify model/data versions and reference uncertainty. Numeric release targets must be chosen before locked evaluation; no accuracy pass from photo-only guesses. |

## 6. Tracking API (no tracking frontend currently found)

For LOG cases run both resource families where applicable. Base paths: `/api/dashboard/weight-logs` and `/api/dashboard/calorie-logs`. Example creates: `{"weight":72.5,"date":"2026-09-07"}` and `{"calories":600,"fat":20,"carbs":70,"protein":35,"date":"2026-09-07"}`.

| ID / priority / layer | Setup and steps | Expected result and persistence checks |
|---|---|---|
| LOG-01 P1 A | POST example, GET list and ID, restart API and reread. | POST 201; GET 200; UUID/owner/date/numbers correct and persistent; weight/calorie resources independent. |
| LOG-02 P1 A | PATCH a numeric field, then date only; GET after each. | Only supplied field changes; original ID/owner/other values preserved. |
| LOG-03 P1 A | PATCH `{}`, explicit null for every nullable update field, unknown-only field and malformed data. | Empty updates 422; null must not violate required DB columns or produce 500. Unknown-only no-op behavior must be explicit; flag schema/database mismatch. |
| LOG-04 P1 A | Weight −1/0/0.01/1000/1000.01; each macro −1/0/decimal; missing field, numeric string, huge magnitude/nonfinite encoding. | Weight >0 and ≤1000; macros ≥0; malformed/nonfinite values rejected safely; no poisoned DB or invalid response serialization. Record coercion policy. |
| LOG-05 P1 A | Filter seeded dates with start only/end only/both/equal endpoints; reverse range; malformed date/leap day. | Inclusive boundaries if implemented as such are verified; no outside-range rows; invalid dates 422; reverse-range behavior documented; stable ordering matches service contract. |
| LOG-06 P0 A | With B cookie GET/PATCH/DELETE A log; try missing and malformed UUID; call all operations unauthenticated. | Foreign/missing IDs 404; malformed UUID 422; unauthenticated 401; no A changes or existence leak. |
| LOG-07 P1 A | DELETE existing ID, GET, DELETE again; create two entries same date; race two PATCHes. | Delete 204 then 404; duplicate-date and last-write behavior documented from persistence; no unintended unique constraint or lost unrelated field update. |
| LOG-08 P2 B+A | Inspect app navigation and prediction result for tracking entry/chart/save control; compare product copy with available routes. | Report missing tracking UI as a product gap, not a passed E2E logging journey. Do not invent nonexistent controls in automation. |

## 7. Streaming chat and conversation persistence

Use `{"type":"run","conversation_id":null,"content":"Hello"}` for a new text turn. Server frames include `state`, `meta`, `token`, terminal `done`/`stopped`/`error`, specialist events, and optional nudge/audio frames. Assert ordered turn events while permitting independent awareness updates.

| ID / priority / layer | Setup and steps | Expected result and persistence checks |
|---|---|---|
| CHAT-01 P0 B+A | Open authenticated `/chat` without WS interception; send a simple message; inspect real frames and DB. | Connection succeeds; meta establishes owned conversation; incremental text appears; terminal result clears loading; one user and completed assistant message persist. |
| CHAT-02 P1 B+A | Send followup with same ID; create new conversation; list and reopen each; refresh/restart backend. | Correct history/order/title/activity ordering; no merged conversations or duplicate optimistic messages; records survive restart. |
| CHAT-03 P1 B+A | Stop midstream and after completion; send replacement while first is streaming; delay stop acknowledgement. | UI promptly displays replacement, stops prior generation, queues next turn correctly, preserves intended partial answer and reaches terminal state; DB/UI agree. Extend existing mocked interruption tests to actual backend. |
| CHAT-04 P1 A+B | Send rapid three followups, stop twice, switch conversation during stream, delete active conversation. | No deadlock, stale token insertion into another thread, duplicate assistant persistence or orphan worker job; explicit behavior for deleted active turns. |
| CHAT-05 P1 A | Send empty/8000/8001-character content, whitespace-only content, invalid JSON, unknown/missing type, malformed UUID, ninth image/fifth asset. | Valid boundary accepted; invalid frames produce controlled error, no provider invocation or corrupt history; usable subsequent valid turn. Document whitespace behavior. |
| CHAT-06 P0 A | Unauthenticated/expired cookie handshake; B list/get/delete A conversation; send A conversation ID in B run/stop/voice frame. | Handshake denied (handler uses policy close 1008); foreign history inaccessible and cannot be appended/deleted/interrupted; no A data in B frames. |
| CHAT-07 P1 B+D | Provider fails before first token, halfway through stream, or hits rate limit/tool recursion cap. | Useful sanitized error, spinner stops, retry works; partial history semantics are consistent; no false claim that a tool completed. |
| CHAT-08 P1 B+A | Drop socket/network midreply; reconnect, refresh and reopen history; restart API during streaming. | Connection status reflects outage; backoff bounded, no automatic duplicate tool execution or lost acknowledged user turn; DB becomes source of truth on recovery. |
| CHAT-09 P1 B+A | Attach 1/8 images and 1/4 assets, remove pending attachment, combine text+attachments, reload completed history. | IDs and thumbnails/documents match submitted turn; removing pending attachment excludes it; failed upload blocks or clearly excludes attachment. |
| CHAT-10 P0 A+D | Supply missing/foreign/malicious attachment IDs; malicious HTML/JS/Markdown links in user, tool and assistant content. | Ownership validation before tools; no script execution or dangerous navigation; IDs never grant filesystem access; safe missing-file UI. |
| CHAT-11 P1 B+A | Open text chat and voice dashboard for A concurrently, then open B; start conflicting A turns and stop one. | Document per-user cancellation policy; no unintended B cancellation, audio or transcript leakage; active-turn cleanup after disconnect. |
| CHAT-12 P1 L+D | Ask ordinary question, food-photo analysis, disconnected Gmail, connected Drive, disabled Blender; force specialist error. | Correct available specialist or ordinary answer; no invented capability; specialist status terminates; tool results/prose remain coherent and no internal secret/debug prompt leaks. |
| CHAT-13 P2 B | Long history/long unbroken text, multiline input, Enter/Shift+Enter, Unicode/emoji, scrolling during stream. | Intended submit/newline behavior, no text clipping/scroll trap, readable transcript, preserved draft; keyboard focus remains usable. |
| CHAT-14 P1 A | DELETE owned conversation then GET/list; delete again; inspect messages and pending memory provenance/jobs. | 204 then 404; no orphan messages; memory policy for deleted source is explicit, jobs cannot resurrect deleted transcript. |

## 8. Voice, screen awareness and proactive messages

| ID / priority / layer | Setup and steps | Expected result and persistence checks |
|---|---|---|
| VOICE-01 P1 B+M+L | Real microphone/TTS, start voice, speak known sentence, finish by silence and manual action. | Off→idle/listening→thinking→speaking→idle; readable transcript, intelligible ordered playback, one conversation turn; PCM sample rate/channel/encoding matches contract. |
| VOICE-02 P1 B+M | Deny/dismiss mic permission, no device, unplug device, browser audio autoplay suspended. Retry after restoring permission. | Clear actionable error, no stuck listening state or uncaught promise, retry works; text chat remains usable. |
| VOICE-03 P1 B+M | Interrupt while thinking, speaking and playing filler; speak over reply; turn voice off and sign out. | Generation/playback stop, queued PCM discarded, no old audio after new answer; capture tracks stop when disabled/signed out. |
| VOICE-04 P1 A+D | Send voice_start at 8000/16000/48000 and invalid rates, silence, empty/odd PCM bytes, binary without start, repeated start/end/cancel. | Valid protocol handled; malformed input bounded and recoverable; cancel/silence creates no unintended turn; no traceback or cross-utterance bytes. |
| VOICE-05 P1 A | Send just below/at/above `MAX_UTTERANCE_BYTES` (1,920,000); repeat accepted sample rates. | Overflow reports utterance-too-long and releases buffer; note limit represents 60s at 16kHz but different duration at 8/48kHz; product limit and UI must agree. |
| VOICE-06 P1 D+B | Disable voice; stop TTS sidecar; fail STT; timeout TTS after partial audio. | Disabled voice rejected explicitly; transcription failure never becomes fabricated speech; text fallback/error truthful; audio terminal cleanup permits next turn. |
| VOICE-07 P2 B+M | Space/orb controls, focus inside input, navigate chat↔dashboard, clear conversation, background/foreground mobile tab. | Shortcuts do not hijack typing; expected shared session/history maintained; no duplicate capture contexts/listeners or stale audio. |
| VOICE-08 P2 M | Run without wake-word artifacts, then with valid supported configuration if available. | Without detector UI offers tap/Space and does not claim active wake-word detection; live detection/false triggers tested only with real supported artifacts. |
| SENSE-01 P1 B+M | Enable eye and choose synthetic window; change visible text; pause/stop through app and browser sharing control. | Explicit permission required; changed frames upload with `kind=screen`; sense carries correct ID; tracks/workers/uploads stop on close. |
| SENSE-02 P1 D+A | Send known timezone/route/activity, invalid/overlong fields and B screen ID; ask about local time/screen. | Valid state updates, invalid payload controlled; forged frame gives no unauthorized content; timezone handling safe; descriptive fields cannot authorize tools. |
| SENSE-03 P1 A+D | Upload >keep screen frames and old timestamp fixtures; list photos; disable vision and try screen upload. | Retention prunes on write and protects newest frame; normal list hides screen frames; disabled upload 403. Verify idle-expired files explicitly: no sweeper is implied by write-time pruning. |
| SENSE-04 P1 B+D | Share unchanged screen, small text edits at high resolution, rapidly changing window; force upload/caption failure. | Sampling/change detection limits traffic; meaningful changes captured within measured delay; error does not freeze UI or claim seeing unavailable content. |
| SENSE-05 P0 D+M | Put synthetic prompt-injection text in screen/mail/document: request secrets, send email, change ownership. | Content treated as untrusted input; no unauthorized tool action, secret disclosure or permission elevation. Use provider substitutes for mutation attempts. |
| NUDGE-01 P1 D+B | Controlled clock: enabled/disabled nudges, active typing/speaking, idle thresholds and minimum gaps. | No unsolicited turn when disabled or inappropriate; bounded eligible nudge, correct start/token/end rendering, no overlap corruption. |
| NUDGE-02 P1 D+B | Dismiss a nudge; simulate ignores and later turns/reconnect/new session. | Dismiss mutes for intended session; ignored nudges back off/mute; no runaway initiation. Document process-local retention/restart behavior. |

## 9. Memory and cognitive inspector

| ID / priority / layer | Setup and steps | Expected result and persistence checks |
|---|---|---|
| MEM-01 P1 A+D | Seed >24 messages; inspect model input for next turn and REST history. | Configured newest history window only goes into model; REST retains full ordered transcript; no other user's context. |
| MEM-02 P1 A+L | A states explicit durable preference; complete assistant reply; run worker, start new thread and ask relevant question. | One idempotent outbox job per completed reply; attributed structured memory persists and is retrieved when relevant. |
| MEM-03 P1 D+A | Restate same fact, correct/retract it, provide temporary/ambiguous statement or assistant-invented fact. | No duplicate fact inflation; correction/retraction respected; extraction stores only supported durable user facts with provenance. |
| MEM-04 P1 A+D | Stop worker after enqueue; stop Redis; restart worker; simulate model/embedding outage and worker crash midjob. | Durable jobs recovered by DB polling, safe retry without duplicate facts; chat/retrieval fail open as designed; errors inspectable and no poison-job busy loop. |
| MEM-05 P0 A+D | B asks for A preferences; delete A account while jobs pending; run two workers against same pending job. | No cross-user retrieval or resurrection, idempotent/concurrency-safe writes; stale jobs terminate safely. |
| MEM-06 P1 D | Disable long-term memory; exceed retrieval limit; use expired/retracted records, missing/malformed embedding. | Disable flag respected, prompt bounded, inactive facts excluded, lexical fallback safe and deterministic where possible. |
| COG-01 P1 B+A | Open Cognitive test panel; inspect rules mode with recent history and attachment counts; snapshot conversation and DB before/after. | Typed perception/interpretation and timing/count metrics, zero online calls; no conversation/tool/memory mutation. |
| COG-02 P1 D+B | Inspect LLM mode with controlled output, then real small test; fail provider/invalid output. | Valid state with one online call; failure returns sanitized 502 and explicitly no action executed; no retry loop or actual delegated action. |
| COG-03 P1 A | Test message 0/1/8000/8001 chars; history 24/25; image 8/9; asset 4/5; capabilities 32/33 and name 120/121; no auth. | Valid boundaries succeed, invalid 422, anonymous 401; inputs cannot grant real capabilities. |
| COG-04 P2 D+B | Missing/corrupt local classifier artifact; repeated/ambiguous input; switch modes and close/reopen while pending. | Safe rules fallback, bounded confidence/signals, honest diagnostics and no stale results assigned to wrong input. |

## 10. Google, documents, assets and Blender

| ID / priority / layer | Setup and steps | Expected result and persistence checks |
|---|---|---|
| INT-01 P1 B+A | List providers with absent configuration, configured disconnected, connected and unknown ID; request authorize when unavailable. | Registry/UI agree; unavailable authorize 503, unknown provider 404; account labels scoped; no secret config in response. |
| INT-02 P1 B+L | Connect dedicated Google account with approved scopes, follow callback, reload; deny consent and repeat. | Successful 303 to configured frontend with connected state; encrypted tokens stored; denial gives useful banner and no partial grant. |
| INT-03 P0 A+D | Callback missing/expired/tampered state, replayed code, B session with A state, missing session, injected error/reason text. | No unauthorized account linking; safe redirect/error; no secret/reflected executable content. Verify state expiry/replay behavior rather than assuming HMAC alone provides it. |
| INT-04 P1 D+A | Missing refresh token/key, insufficient scopes, failed identity fetch, exchange failure; expired access token refresh and upstream revocation. | Correct error/fallback for each branch, no incomplete credentials falsely marked connected; refresh stored securely; reconnect guidance usable. |
| INT-05 P0 A+L | Disconnect test grant twice, then next chat turn; test B access; simulate upstream revoke error. | Idempotent 204 where intended; local access removed, tools no longer available; B unaffected; upstream failure behavior documented without retaining usable unintended access. |
| INT-06 P1 D+L | Gmail-only vs Drive-only grants and independent feature switches; search/read dedicated fixtures; provider quota/timeout. | Tools match scopes/switches; actual requested records returned; helpful sanitized failures; no account crossover. |
| INT-07 P0 D | Ask to write email, then approve exact draft, then alter recipient/body after approval; insert malicious mail instructions. | Drafting does not send; send only after approval of exact recipients/subject/body; changes require renewed app-user approval; provider call ledger proves no unauthorized send. Live send excluded until separately authorized. |
| DOC-01 P1 B+L | Ask for report PDF from dedicated test content; inspect source Doc and exported download, preview and reload conversation. | Tool-created IDs/links real; valid readable PDF with requested content, preview inline, download attachment with correct name; no fabricated URL. |
| DOC-02 P1 D+A | Create Slides/Doc then fail edit/export; retry; move/copy/rename fixture, export DOCX/PPTX. | Partial success reports existing ID; no blind duplicate document creation; formats parse and contain expected data; moves handle old parents. |
| DOC-03 P0 D | Request trash versus permanent delete/share with exact recipients/roles; feed ambiguous filenames and injected document instructions. | Resolve ambiguity before mutation, trash default, permanent deletion only explicitly requested, exact sharing scope; no unauthorized external action. |
| DOC-04 P1 B+A | Upload each supported extension; filename Unicode/path-like/upper-case; invalid PDF/unsupported/empty; cap−1/cap/cap+1 at configured 100 MiB default. | Valid 201 with sanitized metadata; invalid 400; no path traversal; list cap respected; file validation limits documented rather than assuming extension checks validate full contents. |
| DOC-05 P0 A+B | B requests A asset metadata/raw/preview and uses its ID in tools; anonymous request; deleted/missing file link in chat. | 404 for foreign/missing, 401 anonymous on protected paths; clear unavailable-file UI and no preview; no private metadata leakage. |
| DOC-06 P1 B+A | Inspect PDF preview/download and non-PDF `preview=true`; open/close repeatedly; malformed generated markdown link. | Only PDF is inline, others download; `nosniff` and private/no-store headers; safe modal/focus cleanup; malformed link cannot escape intended handling. |
| DOC-07 P0 A+D | Create signed slide-image URL; use valid/expired/tampered token without cookie; attempt signing B image/nonimage. | Only intended PNG accessible for token lifetime; expired/invalid 404, no filesystem escape; bearer link absent from public logs/evidence; private/no-store headers. |
| DOC-08 P0 D | Fetch slide image from loopback/private/credential URL, redirect to private target, oversized or invalid image. | Server-side request forgery and resource exhaustion prevented at every fetch/redirect stage; no internal response leakage or Slides mutation on invalid input. |
| DOC-09 P1 D+L | Search/generate/insert image into test Slides; verify geometry/alt text/credits; omit public base URL; fail insertion after generation. | Valid position and attribution, private assets preserved, missing config explained without regeneration/public sharing; cost and calls bounded. |
| BLEND-01 P1 D+M | Disabled bridge, then enabled isolated Blender scene; upload simple mesh, inspect/import/rig/keyframe/render/save. | Disabled capability unavailable; enabled workflow inspects actual geometry and output, saves new owned artifact without overwriting original; final link downloads. |
| BLEND-02 P0 D+M | Foreign asset/path input, unsupported arbitrary-code operation, bridge outage and malformed response. | No arbitrary Python/filesystem escape or cross-user scene access; controlled error and no invented completion; bridge is not publicly exposed. Shared desktop isolation must be resolved before multiuser deployment. |

## 11. Security, persistence and resilience

| ID / priority / layer | Setup and steps | Expected result and persistence checks |
|---|---|---|
| SEC-01 P0 A | Enumerate all protected routes/methods with no cookie, expired cookie and B cookie over A resources. Include `/mcp/macrovision`. | No bypass through alternate method/path, raw assets, inspector or internal tool endpoint; MCP requires valid user scope/signature. Signed slide-image path is an intentional bounded exception. |
| SEC-02 P0 B+A | From separate allowed and hostile origins exercise credentialed HTTP, form/multipart POST, DELETE and WS handshake. Include deployment subdomain topology. | CORS only allows configured origins; state-changing cookie requests and WebSockets cannot be driven by unauthorized origins. CORS alone is not evidence of CSRF protection. |
| SEC-03 P0 A+D | Tamper MCP user scope, signature, expiry and mount path; run with separate workers/config secrets. | Forged scope denied before tools; user isolation retained; multiworker secret and process-local state limitations explicitly resolved or deployment constrained to one worker. |
| SEC-04 P1 A | On isolated instance ramp repeated signin/signup, inference, LLM inspector, file uploads and WS connections. | Rate/concurrency/resource budgets enforced or missing controls reported; no unlimited credential attempts/provider spend or one-user starvation. Record exact rates, not a generic pass. |
| SEC-05 P1 A+M | Exceed ingress body/frame limits, slow multipart/WS sends, send large concurrent uploads. Monitor memory and temp disk. | Proxy/server rejects within bounded resources, frees buffers, remains responsive; application post-read check alone insufficient. |
| SEC-06 P0 D+B | XSS/path/SQL-like payloads in names, filenames, chat, OAuth reasons and tool responses; inspect rendered output and DB. | Data remains data; no script execution, query injection, header injection or path escape; errors reveal no stack/secrets. |
| DATA-01 P1 A | Apply migrations from empty DB and prior revision; inspect constraints/indexes/cascades; run upgrade again. | One head, repeatable upgrade, expected schema, preserved existing rows; startup against outdated schema fails clearly. |
| DATA-02 P1 A+M | Restart API/Redis/worker independently and entire isolated stack; recover persisted uploads and history. | DB/assets survive expected restart, sessions behave per Redis persistence policy; process-local awareness/nudges/turns reset predictably. |
| DATA-03 P0 M | Backup disposable DB and storage plus necessary encryption configuration; restore to separate environment; sign in and read fixtures. | Coherent owner/history/file references, decryptable test grant, documented key handling; measured recovery time/data-loss window. A created backup without restore test is insufficient. |
| DATA-04 P1 D+A | Simulate DB commit failure, disk full, corrupt metadata JSON and missing raw asset after metadata creation. | No misleading success, safe transaction rollback/read error, bounded orphan handling; B data intact; recovery documented. |
| DATA-05 P0 A+M | Delete test account; inventory DB, Redis, images/assets, screen files, jobs and logs; inspect cache/backups policy. | No continued live access; retention/physical cleanup behavior explicit. Account deletion cannot claim complete erasure while untracked local files remain. Previously downloaded browser files cannot be remotely revoked. |
| PERF-01 P1 A+L | Fixed hardware/config: 1, 5 and 10 concurrent users, each with independent data; warm/cold inference and chat. | Capture p50/p95/error rate, first token/audio, completion latency, RAM/GPU/temp disk and provider calls. Proposed initial target: ordinary CRUD p95 <500ms locally, zero cross-user leaks/unhandled errors; AI budgets set from baseline before release, not arbitrary promises. |
| PERF-02 P2 B+A | 30-minute mixed-use soak: reconnects, canceled turns, voice toggles, uploads and worker activity. | No monotonic growth in tasks, sockets, audio contexts, object URLs, memory/jobs; no leaked active turns. Record start/end resource counts and trend. |

## 12. Browser quality and deployment/publication readiness

| ID / priority / layer | Setup and steps | Expected result and persistence checks |
|---|---|---|
| UI-01 P1 B+M | Chromium, Firefox and WebKit desktop; actual iOS Safari/Android Chrome for camera/mic/screen availability; 360px/768px/1440px widths. | Auth/chat/predict/integrations usable with no overflow or hidden primary control; unsupported hardware features give honest fallback. Existing config only runs Chromium. |
| UI-02 P1 B+M | Keyboard-only navigation, 200% zoom, accessible-name inspection and screen reader on auth, composer, upload, modal and voice controls. | Inputs have programmatic labels, visible focus, logical traversal; dialogs contain/restore focus; errors/status announced without reading every streamed token; no keyboard trap. |
| UI-03 P2 B+M | Reduced motion, high contrast, dark UI readability; loading/empty/error states for every page. | Essential state conveyed beyond color/animation; no blank crash screen; contrast and text legibility measured, not assumed from appearance. |
| UI-04 P1 B | Lose network/recover, session expires during uploads/chat, back/forward navigation, hard refresh each protected URL. | No stale private view after account switch, clear recovery, no duplicate external action on retry; intended route remains accessible after signin. |
| REL-01 P0 M | Prepare proposed publication file manifest without staging; scan working tree and any eventual history for secrets/private content and large binaries. | Exclude `.env`, keys/tokens, user images/assets, chat/evaluation personal data, caches, virtualenvs, weights and unapproved vendor artifacts; scan outputs contain no actual secrets. No automatic GitHub push. |
| REL-02 P1 M | Fresh-copy only intended publishable sources; inspect root/backend/frontend ignores and safe environment templates, sidecar/model licensing and attribution. | New contributor has secret-free example config and artifact acquisition instructions; `.env.example` intentionally available despite current ignore; all distributed code/assets permitted and documented. |
| REL-03 P0 M | Inspect Docker context in disposable build; build backend from clean inputs; inspect image filesystem/layers. | No `var/`, `.env`, real uploads or credentials in any layer; model packaging deliberate; required system tools (including VCS dependency support) available; frozen uv install succeeds. |
| REL-04 P1 M | Follow written setup from clean environment, using Bun/uv and new DB. Start serving API/frontend plus required Redis/worker/TTS explicitly. | Reproducible boot with documented versions/weights; Compose gaps addressed in deployment design; no dependency on absolute developer laptop paths. |
| REL-05 P0 B+M | Serve `bun run build` output through intended HTTPS proxy; direct-load `/chat`; sign in, call `/api`, stream WS, preview/download PDF. | SPA fallback works, `/api` routes to backend, WS upgrades over `wss`, cookies work, long responses survive proxy timeouts; no reliance on Vite dev proxy. |
| REL-06 P0 B+M | Set production cookie/CORS/frontend/OAuth callback/public slide URL; connect test Google account; deny untrusted origin. | HTTPS Secure cookie and correct SameSite behavior, exact callback redirect, no localhost URLs/mixed content/secret VITE variables; externally fetched signed slide image works with only intended access. |
| REL-07 P1 M | `/health` with DB/Redis/model unavailable; restart deployment during live request; cold model warm-up. | Liveness/readiness distinguished, no traffic sent before essential readiness, graceful shutdown/reconnect; a constant health response is not a full readiness pass. |
| REL-08 P0 M | Inspect exposed ports, reverse proxy, filesystem volumes, process identity and Blender/MCP/TTS boundaries. | Only intended public entry points exposed; DB/Redis/desktop bridge private, persistent storage writable only as needed, internal secrets not in frontend bundle. |
| REL-09 P1 M | Deploy new version to disposable staging; run migration and smoke journey; perform documented rollback using compatible schema/backup. | App/data recover, uploads remain, no irreversible migration surprise; runbook identifies schema rollback limitations. |
| REL-10 P1 M | Design CI from existing commands; clean install from lockfiles, run checks, inspect artifacts. | Build/lint/deterministic suites and real-service integration required; private traces not publicly uploaded; live/hardware checks separately reported. No CI currently demonstrated by this plan. |

## 13. Existing automated coverage and remaining work

| Existing files | What they establish when executed successfully | What they do not establish |
|---|---|---|
| `frontend/e2e/chat-interruption.spec.ts` | Two browser cases for stop acknowledgement and replacement message rendering. | Auth, real WebSocket server, database persistence and provider cancellation: these are mocked. |
| `frontend/e2e/document-preview.spec.ts` | Two browser cases for preview/download UI and missing-file display. | Real PDF validity, authorization, Google export and storage: endpoints are mocked. |
| `backend/tests/test_asset_store.py`, `test_drive_tools.py`, `test_slide_images.py` | Asset scoping, selected Drive operations/errors/exports and signed image/image insertion behavior. | Full OAuth browser roundtrip, live Google compatibility, all REST routes and production topology. |
| `backend/tests/test_cognitive_system.py`, `test_cognitive_inspector.py`, `test_cognitive_llm.py` | Rules/classifier/inspector contracts and controlled LLM outputs/errors. | End-to-end chat correctness or live model quality. |
| `backend/tests/test_macrovision_v1_geometry.py` | Selected geometry/fusion calculations. | Entire prediction upload/inference pipeline or real nutritional accuracy. |
| `backend/tests/test_tts_chunker.py` | Text chunking behavior. | Real microphone, STT, sidecar streaming, playback or barge-in. |

No comprehensive auth, tracking CRUD, real-service browser, memory-worker or voice hardware suite was identified in the inspected test directories. Test names/file presence do not constitute passing results or measured coverage.

### Initial commands (run later, in the isolated setup)

From `frontend/`:

```sh
bun install --frozen-lockfile
bun run lint
bun run build
bun run test:e2e
```

From `backend/`, with the disposable environment already selected:

```sh
uv sync --frozen
uv run alembic upgrade head
uv run pytest tests
```

Existing Playwright configuration starts only a Vite dev server at `127.0.0.1:4173` and uses mocked APIs in its current tests. Build a separate real-service Playwright fixture/configuration for the B cases, launch isolated backend/Redis/PostgreSQL and preserve separate user contexts. Do not describe the current command as complete full-stack testing. Use temporary storage and fake provider clients in deterministic backend suites so running them cannot consume personal data or mutate real remote resources.

## 14. Execution sequence and release decision

1. Reproduce AUTH-02/06/08/09/10 and verify isolation; inventory publication/container risks. Fix and regression-test confirmed P0 issues before using public staging.
2. Run existing lint/build/backend/mocked-browser baseline, recording individual outcomes. Add missing deterministic/API auth, tracking, upload, chat and memory cases.
3. Run real-service browser journeys: signup/signin → meal prediction → image chat → followup/stop → refresh/history → logout/replay; separate API tracking CRUD journey; document creation/preview/download journey.
4. Run provider-failure and concurrency cases. Then run bounded live-model/Google read/export tests, and manual voice/camera/screen/Blender checks where enabled.
5. Run production-build staging with actual HTTPS/proxy/cookies/WS, fresh install, backup restore and rollback. Execute the publication manifest review before any future GitHub push.
6. Run measured nutrition validation separately. Technical E2E success does not validate the advertised “most accurate” Combined-mode claim.

Release requires all P0 cases passing, all P1 cases for enabled features passing, and blocked cases explicitly resolved or the affected feature disabled with verified UI/API behavior. Record P2 defects with impact and disposition. No open credential leak, logout replay, cross-user access, unauthorized external mutation or private-data packaging issue is acceptable. Publish a result summary with counts by status/layer, defect links, actual tested environment, live/mock distinction, known limitations and remaining work. **This plan alone is not a release sign-off.**
