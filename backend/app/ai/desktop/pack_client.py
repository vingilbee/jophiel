"""Client for a game pack's local API (stage G; game_packs/minecraft/src/api.js).

Loopback only, bearer token. The token is the configured one, else the file
the pack writes when it starts without one; on a 401 the file is read again
(the pack makes a new token each run). Every call has a short timeout: the
driver loop must never hang on a stuck pack.
"""
from __future__ import annotations

from pathlib import Path

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[3]


class PackError(Exception):
    """The pack is unreachable or refused. ``status`` is the HTTP status, if any."""

    def __init__(self, message: str, status: int | None = None, body: dict | None = None):
        super().__init__(message)
        self.status = status
        self.body = body or {}


class PackClient:
    def __init__(self, url: str, *, token: str = "", token_file: str | None = None, timeout: float = 5.0):
        self.url = url.rstrip("/")
        self._fixed_token = token
        self._token_file = token_file
        self._token: str | None = token or None
        self._timeout = timeout

    @classmethod
    def from_settings(cls) -> PackClient:
        from app.core.config import get_settings

        settings = get_settings()
        return cls(settings.minecraft_pack_url, token=settings.minecraft_pack_token,
                   token_file=settings.minecraft_pack_token_file)

    def _read_token(self) -> str | None:
        if self._fixed_token:
            return self._fixed_token
        if not self._token_file:
            return None
        path = Path(self._token_file)
        path = path if path.is_absolute() else BACKEND_ROOT / path
        try:
            return path.read_text().strip() or None
        except OSError:
            return None

    def _call(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None,
              timeout: float | None = None) -> dict | list | None:
        for attempt in (0, 1):
            token = self._token or self._read_token()
            if not token:
                raise PackError("no pack token (is the pack running?)")
            self._token = token
            try:
                response = httpx.request(
                    method, self.url + path, json=json, params=params,
                    headers={"authorization": f"Bearer {token}"},
                    timeout=timeout or self._timeout,
                )
            except httpx.HTTPError as exc:
                raise PackError(f"pack unreachable: {type(exc).__name__}") from exc
            if response.status_code == 401 and attempt == 0:
                self._token = None  # the pack restarted with a new token
                continue
            try:
                body = response.json()
            except ValueError:
                body = None
            if response.status_code >= 400:
                message = body.get("error") if isinstance(body, dict) else response.text[:200]
                raise PackError(message or f"HTTP {response.status_code}", response.status_code,
                                body if isinstance(body, dict) else None)
            return body
        raise PackError("unauthorized", 401)

    # -- the API --------------------------------------------------------------

    def health(self) -> dict:
        return self._call("GET", "/health", timeout=2.0)

    def state(self) -> dict:
        return self._call("GET", "/state")

    def manifest(self) -> list[dict]:
        return self._call("GET", "/skills")

    def run(self, skill: str, params: dict, *, source: str = "driver") -> dict:
        return self._call("POST", "/skills/run", json={"skill": skill, "params": params, "source": source})

    def get_run(self, run_id: str, *, wait: float = 0) -> dict:
        return self._call("GET", f"/skills/runs/{run_id}", params={"wait": wait} if wait else None,
                          timeout=wait + 3.0)

    def current(self) -> dict | None:
        return self._call("GET", "/skills/current")

    def stop(self) -> dict | None:
        return self._call("POST", "/skills/stop", timeout=2.0)

    def recipe(self, item: str) -> dict:
        """What it takes to make ``item`` (lowercase Minecraft id), against what she carries."""
        return self._call("GET", f"/recipes/{item}")

    def modes(self) -> dict:
        """Background modes: ``{"guard": bool, "gather": [block names], "pickup": bool}``."""
        return self._call("GET", "/modes")

    def set_modes(self, modes: dict) -> dict:
        """Set the modes given (the rest stay); 400 for an unknown block name."""
        return self._call("POST", "/modes", json=modes)

    def notices(self, since: int = 0, *, wait: float = 0) -> dict:
        return self._call("GET", "/notices", params={"since": since, "wait": wait}, timeout=wait + 3.0)

    def start_program(self, code: str, *, seconds: float | None = None) -> dict:
        """Run her code in the pack's sandbox (stage H): ``{"id", "status": "running"}``; 409 busy."""
        body: dict = {"code": code}
        if seconds is not None:
            body["seconds"] = seconds
        return self._call("POST", "/programs", json=body)

    def get_program(self, program_id: str, *, wait: float = 0) -> dict:
        """The program: status, reason, value, calls, log, seconds (waits up to ``wait`` s)."""
        return self._call("GET", f"/programs/{program_id}", params={"wait": wait} if wait else None,
                          timeout=wait + 3.0)

    def block_census(self, x: float, y: float, z: float, radius: int = 10) -> dict:
        """``{center, radius, counts: {block: n}, unloaded}``: blocks around a spot, air left out."""
        return self._call("GET", "/blocks", params={"x": x, "y": y, "z": z, "r": radius})
