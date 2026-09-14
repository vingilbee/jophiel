"""User-scoped Workspace MCP tools, launched with a short-lived OAuth token."""
from __future__ import annotations

import json
import os
import uuid
from typing import Literal

from mcp.server.fastmcp import FastMCP

from app.ai.tools.drive.api import FILE_FIELDS, file_id, request
from app.ai.tools.drive import image_tools
from app.services import asset_store

mcp = FastMCP("drive", log_level="WARNING")
EXPORTS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
    "csv": "text/csv",
}


def _user() -> uuid.UUID:
    return uuid.UUID(os.environ["DRIVE_USER_ID"])


def _artifact(data: bytes, filename: str) -> dict:
    record = asset_store.save_bytes(_user(), data, filename=filename, kind="generated")
    url = f"/api/assets/{record.asset_id}/raw"
    label = record.filename.replace("[", "").replace("]", "").replace("\n", " ")
    return {**record.to_dict(), "download_url": url,
            "download_link": f"[{label}]({url})"}


@mcp.tool()
async def drive_list_files(query: str = "trashed = false", page_token: str = "", page_size: int = 50) -> dict:
    """Search Drive using Google query syntax, e.g. name contains 'report' or 'FOLDER_ID' in parents. Follow nextPageToken for more results."""
    params = {"q": query, "pageSize": max(1, min(page_size, 100)),
              "fields": f"nextPageToken,files({FILE_FIELDS})", "supportsAllDrives": "true",
              "includeItemsFromAllDrives": "true"}
    if page_token:
        params["pageToken"] = page_token
    return await request("drive", "GET", "/files", params=params)


@mcp.tool()
async def drive_get_file(id: str) -> dict:
    """Read file metadata, parents, and editor link. Use docs_get or slides_get for Workspace content."""
    return await request("drive", "GET", f"/files/{file_id(id)}", params={"fields": FILE_FIELDS, "supportsAllDrives": "true"})


@mcp.tool()
async def drive_create_folder(name: str, parent_id: str = "") -> dict:
    """Create a Drive folder, optionally inside a parent folder."""
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [file_id(parent_id)]
    return await request("drive", "POST", "/files", body=body, params={"fields": FILE_FIELDS, "supportsAllDrives": "true"})


@mcp.tool()
async def drive_update_file(id: str, name: str | None = None, description: str | None = None, starred: bool | None = None, trashed: bool | None = None) -> dict:
    """Rename, describe, star, trash (trashed=true), or restore (trashed=false) a file/folder. Trashing is the default way to remove a file."""
    body = {k: v for k, v in {"name": name, "description": description, "starred": starred, "trashed": trashed}.items() if v is not None}
    if not body:
        raise ValueError("Provide at least one change.")
    return await request("drive", "PATCH", f"/files/{file_id(id)}", body=body, params={"fields": FILE_FIELDS, "supportsAllDrives": "true"})


@mcp.tool()
async def drive_move_file(id: str, destination_folder_id: str) -> dict:
    """Move a file/folder to a destination folder. Reads current parents before moving."""
    current = await drive_get_file(id)
    destination = file_id(destination_folder_id)
    parents = current.get("parents", [])
    if destination in parents:
        return current
    params = {"addParents": destination, "fields": FILE_FIELDS, "supportsAllDrives": "true"}
    if parents:
        params["removeParents"] = ",".join(parents)
    return await request("drive", "PATCH", f"/files/{file_id(id)}", body={}, params=params)


@mcp.tool()
async def drive_copy_file(id: str, name: str = "", destination_folder_id: str = "") -> dict:
    """Copy a file, including a Docs/Slides template. Google does not copy folders with this operation."""
    body = {}
    if name:
        body["name"] = name
    if destination_folder_id:
        body["parents"] = [file_id(destination_folder_id)]
    return await request("drive", "POST", f"/files/{file_id(id)}/copy", body=body, params={"fields": FILE_FIELDS, "supportsAllDrives": "true"})


@mcp.tool()
async def drive_delete_permanently(id: str, explicitly_requested: bool = False) -> dict:
    """Irreversibly delete a file/folder. Only use when the user explicitly requested permanent deletion; otherwise trash with drive_update_file."""
    if not explicitly_requested:
        raise ValueError("Permanent deletion must be explicitly requested; use trash instead.")
    return await request("drive", "DELETE", f"/files/{file_id(id)}", params={"supportsAllDrives": "true"})


@mcp.tool()
async def drive_list_permissions(id: str, page_token: str = "") -> dict:
    """List who can access a file. Follow nextPageToken if present."""
    params = {"fields": "nextPageToken,permissions(id,type,role,emailAddress,domain)", "supportsAllDrives": "true"}
    if page_token:
        params["pageToken"] = page_token
    return await request("drive", "GET", f"/files/{file_id(id)}/permissions", params=params)


@mcp.tool()
async def drive_share_file(id: str, role: Literal["reader", "commenter", "writer"], recipient_type: Literal["user", "group", "domain", "anyone"] = "user", recipient: str = "", notify: bool = False) -> dict:
    """Grant requested access to a user/group email, domain, or anyone. Public sharing must be requested. Email notification defaults off."""
    body = {"type": recipient_type, "role": role}
    if recipient_type != "anyone" and not recipient:
        raise ValueError("A recipient is required.")
    if recipient_type in {"user", "group"}:
        body["emailAddress"] = recipient
    elif recipient_type == "domain":
        body["domain"] = recipient
    return await request("drive", "POST", f"/files/{file_id(id)}/permissions", body=body, params={"sendNotificationEmail": str(notify).lower(), "supportsAllDrives": "true", "fields": "id,type,role,emailAddress"})


@mcp.tool()
async def drive_remove_permission(id: str, permission_id: str) -> dict:
    """Revoke a specific sharing permission returned by drive_list_permissions."""
    return await request("drive", "DELETE", f"/files/{file_id(id)}/permissions/{file_id(permission_id)}", params={"supportsAllDrives": "true"})


@mcp.tool()
async def drive_upload_asset(asset_id: str, name: str = "", parent_id: str = "") -> dict:
    """Upload a user-owned chat attachment/generated file to Drive. No local filesystem paths accepted."""
    data, record = asset_store.read_bytes(_user(), asset_id)
    metadata = {"name": name or record.filename}
    if parent_id:
        metadata["parents"] = [file_id(parent_id)]
    boundary = uuid.uuid4().hex
    content = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
               + json.dumps(metadata).encode()
               + f"\r\n--{boundary}\r\nContent-Type: {record.media_type}\r\n\r\n".encode()
               + data + f"\r\n--{boundary}--\r\n".encode())
    return await request("upload", "POST", "/files", content=content,
                         content_type=f"multipart/related; boundary={boundary}",
                         params={"uploadType": "multipart", "fields": FILE_FIELDS, "supportsAllDrives": "true"})


@mcp.tool()
async def drive_export_file(id: str, format: Literal["pdf", "docx", "pptx", "xlsx", "txt", "csv"] = "pdf") -> dict:
    """Export a Google Workspace file into a downloadable chat attachment. PDF works for Docs/Slides/Sheets; docx for Docs, pptx for Slides, xlsx/csv for Sheets. Return download_link verbatim to the user."""
    metadata = await drive_get_file(id)
    data = await request("drive", "GET", f"/files/{file_id(id)}/export", params={"mimeType": EXPORTS[format]}, binary=True)
    return _artifact(data, f"{metadata['name']}.{format}")


@mcp.tool()
async def drive_download_file(id: str) -> dict:
    """Download a binary Drive document/image/mesh as a user-owned chat attachment. For native Google Docs/Slides use drive_export_file."""
    metadata = await drive_get_file(id)
    if metadata["mimeType"].startswith("application/vnd.google-apps."):
        raise ValueError("Use drive_export_file for Google Workspace files.")
    data = await request("drive", "GET", f"/files/{file_id(id)}", params={"alt": "media", "supportsAllDrives": "true"}, binary=True)
    return _artifact(data, metadata["name"])


@mcp.tool()
async def docs_create(title: str, text: str = "") -> dict:
    """Create a Google Doc and insert its initial plain text. Use docs_batch_update afterward for headings, formatting, images, and tables."""
    doc = await request("docs", "POST", "/documents", body={"title": title})
    id = doc["documentId"]
    result = {"id": id, "url": f"https://docs.google.com/document/d/{id}/edit"}
    if text:
        try:
            await docs_batch_update(id, [{"insertText": {"location": {"index": 1}, "text": text}}])
        except ValueError as exc:
            return {**result, "content_inserted": False, "error": str(exc)}
    return result


@mcp.tool()
async def docs_get(id: str) -> dict:
    """Read a Google document, including all tabs, text, styles, and structural indices."""
    return await request("docs", "GET", f"/documents/{file_id(id)}", params={"includeTabsContent": "true"})


@mcp.tool()
async def docs_batch_update(id: str, requests: list[dict], required_revision_id: str = "") -> dict:
    """Apply Google Docs API requests: insertText, replaceAllText, updateParagraphStyle, updateTextStyle, insertTable, insertInlineImage, deleteContentRange, etc. Indices are UTF-16 offsets; use docs_get first when editing. Optional revision prevents overwriting concurrent changes."""
    body = {"requests": requests}
    if required_revision_id:
        body["writeControl"] = {"requiredRevisionId": required_revision_id}
    return await request("docs", "POST", f"/documents/{file_id(id)}:batchUpdate", body=body)


@mcp.tool()
async def slides_create(title: str) -> dict:
    """Create an empty Google Slides presentation. Populate it with slides_batch_update."""
    result = await request("slides", "POST", "/presentations", body={"title": title})
    id = result["presentationId"]
    return {"id": id, "url": f"https://docs.google.com/presentation/d/{id}/edit"}


@mcp.tool()
async def slides_get(id: str) -> dict:
    """Read presentation slides, page elements, text, dimensions, and object IDs before editing."""
    return await request("slides", "GET", f"/presentations/{file_id(id)}")


@mcp.tool()
async def slides_batch_update(id: str, requests: list[dict], required_revision_id: str = "") -> dict:
    """Apply Google Slides API requests: createSlide, createShape, insertText, createImage, updateTextStyle, updateShapeProperties, replaceAllText, deleteObject, etc. Use explicit objectIds when creating slides/shapes for subsequent requests."""
    body = {"requests": requests}
    if required_revision_id:
        body["writeControl"] = {"requiredRevisionId": required_revision_id}
    return await request("slides", "POST", f"/presentations/{file_id(id)}:batchUpdate", body=body)


@mcp.tool()
async def image_search(query: str, limit: int = 5) -> dict:
    """Find relevant Wikimedia Commons photos/illustrations, with image URLs, dimensions, licenses, and attribution. Pass a result's image_url, attribution, and source_url to slides_insert_image. No search API key required."""
    return await image_tools.search_images(query, limit)


@mcp.tool()
async def image_generate(prompt: str, size: Literal["1024x1024", "1536x1024", "1024x1536"] = "1536x1024", quality: Literal["low", "medium", "high"] = "medium") -> dict:
    """Generate one original slide illustration using the configured OpenAI image model. Describe subject, composition, and visual style. Returns a private asset_id for slides_insert_image and download link. Uses paid image API; reuse generated assets across slides."""
    return await image_tools.generate_image(_user(), prompt, size, quality)


@mcp.tool()
async def slides_insert_image(presentation_id: str, slide_id: str, asset_id: str = "", image_id: str = "", image_url: str = "", x: float = 40, y: float = 80, width: float = 300, height: float = 240, alt_text: str = "", attribution: str = "", source_url: str = "") -> dict:
    """Insert and position an image in Slides. Supply exactly one asset_id (generated/uploaded file), image_id (chat photo), or public HTTPS image_url (search result). Coordinates and box size are in points; use slides_get for page and slide IDs. Include search result attribution/source_url to add a linked credit below the image. Local images use a 15-minute signed fetch URL and require SLIDE_IMAGE_PUBLIC_BASE_URL."""
    return await image_tools.insert_image(_user(), presentation_id, slide_id, asset_id, image_id, image_url, x, y, width, height, alt_text, attribution, source_url)


if __name__ == "__main__":
    mcp.run(transport="stdio")
