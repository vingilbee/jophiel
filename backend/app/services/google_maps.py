"""The Maps web APIs. Expose only fields used by the phone and chat tools."""
from __future__ import annotations

import json
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Literal
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import httpx

from app.core.config import get_settings

Mode = Literal["drive", "two_wheeler", "walk", "transit", "bicycle"]
Point = tuple[float, float]
MODES = {"drive": "DRIVE", "two_wheeler": "TWO_WHEELER", "walk": "WALK",
         "transit": "TRANSIT", "bicycle": "BICYCLE"}

# https://developers.google.com/maps/documentation/routes/coverage-two-wheeled
TWO_WHEELER_REGIONS = frozenset(
    "AR BD BJ BO BR CL CO CR DZ EC EG GH GT HK HN ID IN KE KH LA LK MM MX MY NG NI "
    "PE PH PK PY RW SG TG TH TN TW UG UY VN ZA".split()
)
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places"
PLACES_URL = f"{PLACE_DETAILS_URL}:searchText"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GEOCODING_URL = "https://geocode.googleapis.com/v4/geocode/location"
PLACE_FIELDS = ("id", "displayName", "formattedAddress", "location")
BASIC_FIELDS = ",".join(f"places.{field}" for field in PLACE_FIELDS)
HOURS_FIELDS = ",places.currentOpeningHours.openNow,places.regularOpeningHours.weekdayDescriptions"
TIMEOUT = 5.0


class MapsUnavailable(RuntimeError):
    """Provider unavailable, quota exceeded, credentials missing or an invalid response."""


@dataclass(frozen=True)
class Place:
    name: str
    address: str
    point: Point
    place_id: str
    country: str | None = None
    open_now: bool | None = None
    hours: tuple[str, ...] = ()


@dataclass(frozen=True)
class Waypoint:
    address: str | None = None
    place_id: str | None = None
    point: Point | None = None

    def request_data(self) -> dict:
        if self.place_id:
            return {"placeId": self.place_id}
        if self.point is not None:
            return {"location": {"latLng": _lat_lng(self.point)}}
        return {"address": self.address}


@dataclass(frozen=True)
class Route:
    distance_m: int
    duration_s: float
    mode: Mode
    traffic_aware: bool
    fell_back: bool


def _lat_lng(point: Point) -> dict[str, float]:
    return {"latitude": point[0], "longitude": point[1]}


def _headers(fields: str) -> dict[str, str]:
    key = get_settings().google_maps_api_key
    if not key:
        raise MapsUnavailable
    return {"X-Goog-Api-Key": key, "X-Goog-FieldMask": fields}


def _post(url: str, body: dict, fields: str) -> httpx.Response:
    try:
        return httpx.post(url, json=body, headers=_headers(fields), timeout=TIMEOUT)
    except httpx.HTTPError:
        raise MapsUnavailable from None


def _response_data(response: httpx.Response) -> dict:
    if not response.is_success:
        raise MapsUnavailable
    try:
        body = response.json()
    except ValueError:
        raise MapsUnavailable from None
    if not isinstance(body, dict):
        raise MapsUnavailable
    return body


def search(query: str, *, near: Point | None = None, hours: bool = False, country: bool = False) -> list[Place]:
    fields = BASIC_FIELDS
    if hours:
        fields += HOURS_FIELDS
    if country:
        fields += ",places.addressComponents"
    body: dict = {"textQuery": query, "pageSize": 5}
    if near is not None:
        body["locationBias"] = {"circle": {"center": _lat_lng(near), "radius": 5000.0}}
    values = _response_data(_post(PLACES_URL, body, fields))
    return [place for item in values.get("places", []) if (place := _place(item)) is not None]


def place_details(place_id: str, *, country: bool = False) -> Place | None:
    fields = ",".join(PLACE_FIELDS) + (",addressComponents" if country else "")
    try:
        url = f"{PLACE_DETAILS_URL}/{quote(place_id, safe='')}"
        response = httpx.get(url, headers=_headers(fields), timeout=TIMEOUT)
    except httpx.HTTPError:
        raise MapsUnavailable from None
    if response.status_code == 404:
        return None
    return _place(_response_data(response))


def _place(item: dict) -> Place | None:
    location = item.get("location")
    name = item.get("displayName", {}).get("text")
    if location is None or not name:
        return None
    # Protobuf JSON can omit zero fields.
    point = (location.get("latitude", 0.0), location.get("longitude", 0.0))
    return Place(
        name=name, address=item.get("formattedAddress", ""), point=point, place_id=item.get("id", ""),
        country=_country(item, "shortText"),
        open_now=item.get("currentOpeningHours", {}).get("openNow"),
        hours=tuple(item.get("regularOpeningHours", {}).get("weekdayDescriptions", [])),
    )


def _country(result: dict, field: str) -> str | None:
    return next((part.get(field) for part in result.get("addressComponents", [])
                 if "country" in part.get("types", [])), None)


def _compute(origin: Waypoint, destination: Waypoint, mode: Mode) -> dict | None:
    body = {"origin": origin.request_data(), "destination": destination.request_data(), "travelMode": MODES[mode]}
    if mode in ("drive", "two_wheeler"):
        body["routingPreference"] = "TRAFFIC_AWARE"
    response = _post(ROUTES_URL, body, "routes.distanceMeters,routes.duration,fallbackInfo.routingMode")
    if mode == "two_wheeler" and response.status_code in (400, 404):
        # Routes rejects a two-wheeler request where that travel mode is unavailable.
        return None
    data = _response_data(response)
    if not data.get("routes"):
        return None
    return {
        **data["routes"][0],
        "traffic_aware": mode in ("drive", "two_wheeler")
        and data.get("fallbackInfo", {}).get("routingMode") != "FALLBACK_TRAFFIC_UNAWARE",
    }


def route(origin: Waypoint, destination: Waypoint, mode: Mode) -> Route | None:
    row = _compute(origin, destination, mode)
    fell_back = row is None and mode == "two_wheeler"
    if fell_back:
        mode = "drive"
        row = _compute(origin, destination, mode)
    if row is None:
        return None
    try:
        duration = row["duration"]
        if not duration.endswith("s"):
            raise ValueError
        return Route(row.get("distanceMeters", 0), float(duration[:-1]), mode, row["traffic_aware"], fell_back)
    except (KeyError, ValueError, AttributeError, TypeError):
        raise MapsUnavailable from None


def _reverse(point: Point, fields: str) -> list[dict]:
    # Coordinates go in Google's query string. urllib doesn't log request URLs; keep provider errors private.
    query = urlencode({"location.latitude": point[0], "location.longitude": point[1]})
    request = Request(f"{GEOCODING_URL}?{query}", headers=_headers(fields))
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response).get("results", [])
    except (URLError, OSError, ValueError, AttributeError):
        raise MapsUnavailable from None


def address_at(point: Point) -> str | None:
    results = _reverse(point, "results.formattedAddress,results.types")
    for result in results:
        if result.get("formattedAddress") and set(result.get("types", [])) - {"plus_code"}:
            return result["formattedAddress"]
    return None


def country_at(point: Point) -> str | None:
    results = _reverse(point, "results.addressComponents.shortText,results.addressComponents.types")
    return next((country for result in results if (country := _country(result, "shortText"))), None)


def distance_m(start: Point, end: Point) -> int:
    a_lat, a_lng = map(radians, start)
    b_lat, b_lng = map(radians, end)
    h = sin((b_lat - a_lat) / 2) ** 2 + cos(a_lat) * cos(b_lat) * sin((b_lng - a_lng) / 2) ** 2
    return round(12_742_000 * asin(sqrt(min(1.0, h))))
