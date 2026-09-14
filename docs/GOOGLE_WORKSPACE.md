# Google Drive document tools

The `drive_agent` specialist uses the signed-in app user's connected Google
account. It can search files, read metadata, create folders, rename, move,
copy, trash, restore, permanently delete, manage sharing, upload chat assets,
download files, create/read/edit Docs and Slides, and export PDF/Office files.
Docs and Slides batch-update tools expose their native editing operations,
including formatting, tables, images, and slide layouts. Google file permissions
and API limits still apply; this is not every administrative Drive API.

## Connect the dedicated agent account

1. In the Google Cloud project, enable Gmail API, Google Drive API, Google Docs
   API, and Google Slides API. Configure the OAuth consent screen and add the
   dedicated account as a test user if the app is in testing mode.
2. Configure a Web application OAuth client with the exact redirect URI in
   `GOOGLE_OAUTH_REDIRECT_URI` (default
   `http://localhost:8000/api/integrations/google/callback`). Set
   `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
   `GOOGLE_TOKEN_ENCRYPTION_KEY` in `backend/.env`.
3. Optionally set `GOOGLE_ACCOUNT_USERNAME` to the dedicated account's email.
   This preselects it in Google's sign-in flow. `GOOGLE_ACCOUNT_PASSWORD` is
   deliberately unused: Google Workspace APIs require OAuth tokens.
4. Sign in to the app, open Integrations, and Connect Google (or Enable Drive
   for an existing Gmail-only connection). Consent once with the dedicated
   account. The backend encrypts the refresh token and refreshes access
   automatically on later chat turns. Revoked/expired grants need reconnecting.

The app does not attach a shared environment account to every app user. Google
connections and exported artifacts remain scoped to the authenticated app user.
Full `https://www.googleapis.com/auth/drive` access is requested because the
tools manage existing files, not just files created by this app. Drive scope
also authorizes the Docs and Slides API operations. Users may grant Gmail or
Drive independently; only the granted toolsets are exposed.

## PDF previews

Ask “Create a weekly report as a PDF.” The specialist creates a Doc or Slides
source, exports it, and saves the bytes in the configured user-scoped asset
store. Its returned Markdown link becomes a file card in chat. Preview serves
PDF inline through authenticated `/api/assets/{id}/raw?preview=true`; Download
serves it as an attachment. The same card renders from saved conversation text.
Exported files stay local even when the Google source changes or is deleted.

Supported local attachments: PDF, DOCX, PPTX, XLSX, TXT, CSV, PNG/JPEG and the
existing Blender/mesh formats. Google Workspace exports have Google's 10 MB
export limit; local storage also enforces `ASSET_MAX_UPLOAD_MB`. Set
`DRIVE_TOOLS_ENABLED=false` to disable the specialist.

## Images in presentations

The Drive specialist has three image tools:

- `image_search(query)`: searches Wikimedia Commons for PNG/JPEG/GIF images and
  returns public image URLs, dimensions, author, license, source page, and credit
  text. No additional API key is needed. This is Commons search, not a general
  web image search engine. Missing licenses should be checked on the source page
  or a different result selected.
- `image_generate(prompt, size, quality)`: uses the existing `OPENAI_API_KEY` and
  `SLIDE_IMAGE_MODEL` (default `gpt-image-2`) to create one original PNG, stored as
  a private, user-owned asset. This calls the paid OpenAI image API. Its returned
  `asset_id` can be reused across slides.
- `slides_insert_image(...)`: accepts exactly one `image_url` from search,
  `asset_id` from generation/file upload, or `image_id` from an attached chat
  photo. It validates the slide and bounding box, inserts the image, and adds
  linked credits when attribution/source information is supplied. Coordinates
  are in points. Images fit in the requested box; leave enough room for captions.

Public search image URLs work with a localhost backend. For **generated or
uploaded images**, set `SLIDE_IMAGE_PUBLIC_BASE_URL` to a publicly reachable
HTTPS origin that routes `/api/assets/slide-image/*` to this backend. For local
development, this needs an HTTPS tunnel or a deployed backend. The feature does
not open a tunnel automatically. Do not use `localhost` or a private IP here.

That endpoint accepts a signed, 15-minute, image-specific token instead of a
session cookie so Google's servers can fetch the image. The normal asset routes
still require user authentication. Local images are decoded, stripped of metadata,
and resized to at most 2048 pixels per side before exposure. Signed links use
the existing `GOOGLE_TOKEN_ENCRYPTION_KEY` with a distinct `slide-image` purpose;
the originating user and exact asset are part of the signed payload. Treat these
temporary URLs as bearer links and redact `/api/assets/slide-image/*` tokens in
reverse-proxy/access logs. They are sent to Google but never returned in the
agent's tool result. Google retains its inserted copy after the URL expires.

Example: “Create a five-slide presentation about balanced meals with relevant
photos and source credits.” For original art, ask for “a consistent set of
generated illustrations.” If public image hosting isn't configured, generated
images can still be downloaded, but local-image insertion reports the setup
requirement instead of claiming success.

## Validation

Backend: `uv run python -m pytest tests/test_asset_store.py tests/test_drive_tools.py tests/test_slide_images.py`

Frontend: `bun run build` and `bun run test:e2e -- e2e/document-preview.spec.ts`

Tests use mocked Google responses and browser API routes; a live Google account
is required to verify real Cloud configuration, consent, and end-to-end exports.

References: [Google OAuth](https://developers.google.com/identity/protocols/oauth2/web-server),
[Drive API](https://developers.google.com/workspace/drive/api/reference/rest/v3),
[Docs batch updates](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate),
[Slides API](https://developers.google.com/workspace/slides/api/reference/rest),
[Workspace exports](https://developers.google.com/workspace/drive/api/guides/manage-downloads).

Image references: [OpenAI image generation](https://developers.openai.com/api/docs/guides/image-generation),
[Wikimedia image metadata](https://www.mediawiki.org/wiki/API:Imageinfo),
[Google Slides image insertion](https://developers.google.com/workspace/slides/api/guides/add-image).
