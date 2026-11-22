"""The reply agent's Maps tools. Coordinates stay inside the service calls."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Literal

from langchain_core.tools import BaseTool, tool

from app.core.config import get_settings
from app.services import google_maps, phone_actions
from app.services.google_maps import Mode, Waypoint

MAPS_UNAVAILABLE = "Maps is unavailable right now."
MODE_LABELS = {"drive": "car", "two_wheeler": "motorcycle", "walk": "walking",
               "transit": "public transit", "bicycle": "bicycle"}
TWO_WHEELER_FALLBACK = "Two-wheeler routing is unavailable here. Using driving instead."
COORDINATE_PAIR = re.compile(r"[-+]?\d{1,3}\.\d+\s*[,; ]\s*[-+]?\d{1,3}\.\d+")


def _query(value: str) -> str:
    return " ".join(value.split())[:300]


def _display(value: str) -> str:
    return COORDINATE_PAIR.sub("a location", value)


def build_phone_tools(user_id: uuid.UUID, conversation_id: uuid.UUID) -> list[BaseTool]:
    found: dict[str, google_maps.Place] = {}

    async def ask(action: str, args: dict, target: tuple[str, str | None] | None = None) -> phone_actions.Outcome:
        return await asyncio.to_thread(phone_actions.act, user_id, conversation_id, action, args, target=target)

    async def position(target: tuple[str, str | None] | None = None) -> phone_actions.Outcome:
        outcome = await ask("get_position", {}, target)
        if outcome.point is not None:
            await asyncio.to_thread(phone_actions.record_position_use, user_id, conversation_id, outcome)
        return outcome

    async def waypoint(value: str, saved: bool, target: tuple[str, str | None] | None = None) -> Waypoint | str:
        if saved:
            result = await ask("resolve_saved_place", {"name": value}, target)
            return Waypoint(point=result.point) if result.point is not None else result.text
        if value in found:
            return Waypoint(place_id=found[value].place_id)
        return Waypoint(address=value)

    @tool
    async def phone_action(
        action: Literal["navigate_to"], destination: str, mode: Mode = "drive",
        saved_place: bool = False,
    ) -> str:
        """Start Google Maps navigation on the user's phone for this destination.

        Destination is a place name, an address, or a place_id returned by search_places.
        Set saved_place for one of the user's saved names, such as home or work. Pick mode from
        the user's request and recent motion, defaulting to drive. A new trip replaces the current trip.
        """
        value = _query(destination)
        if not value:
            return "A navigation destination is needed."
        target = await asyncio.to_thread(phone_actions.target_for, user_id) if saved_place else None
        place = None
        point = None
        if saved_place:
            result = await waypoint(value, True, target)
            if isinstance(result, str):
                return result
            point = result.point
        else:
            place = found.get(value)
            if place is None and get_settings().google_maps_api_key:
                try:
                    matches = await asyncio.to_thread(google_maps.search, value, country=mode == "two_wheeler")
                    place = matches[0] if matches else None
                except google_maps.MapsUnavailable:
                    pass
            if place is not None:
                point = place.point
        fell_back = False
        if mode == "two_wheeler":
            country = place.country if place else None
            if country is None and point is not None:
                try:
                    country = await asyncio.to_thread(google_maps.country_at, point)
                except google_maps.MapsUnavailable:
                    pass
            if country not in google_maps.TWO_WHEELER_REGIONS:
                mode, fell_back = "drive", True
        args = {
            "name": _display(place.name if place else value),
            "lat": point[0] if point is not None else None,
            "lng": point[1] if point is not None else None,
            "query": value if point is None else None,
            "mode": mode,
        }
        if place and place.place_id:
            args["place_id"] = place.place_id
        outcome = await ask(action, args, target)
        return f"{TWO_WHEELER_FALLBACK} {outcome.text}" if fell_back else outcome.text

    @tool
    async def get_route(
        destination: str, origin: str | None = None, mode: Mode = "drive",
        origin_saved_place: bool = False, destination_saved_place: bool = False,
    ) -> str:
        """Estimate travel time and distance. Driving and supported two-wheeler routes account for live traffic.

        Use the requested travel mode or pick from recent motion, defaulting to drive. Omit origin to use
        the phone's position once. Pass an address, a place_id from search_places, or set the relevant
        saved_place flag when origin or destination is a saved name such as home or work.
        """
        if not get_settings().google_maps_api_key:
            return MAPS_UNAVAILABLE
        value = _query(destination)
        start = _query(origin) if origin is not None else None
        if not value or start == "" or (origin_saved_place and start is None):
            return "A valid route destination and starting point are needed."
        target = None
        if origin_saved_place or destination_saved_place:
            target = await asyncio.to_thread(phone_actions.target_for, user_id)
        end = await waypoint(value, destination_saved_place, target)
        if isinstance(end, str):
            return end
        if start is None:
            result = await position(target)
            if result.point is None:
                return result.text
            beginning = Waypoint(point=result.point)
        else:
            beginning = await waypoint(start, origin_saved_place, target)
            if isinstance(beginning, str):
                return beginning
        try:
            route = await asyncio.to_thread(google_maps.route, beginning, end, mode)
        except google_maps.MapsUnavailable:
            return MAPS_UNAVAILABLE
        if route is None:
            return "Maps couldn't find a route."
        minutes = max(1, round(route.duration_s / 60))
        distance = f"{route.distance_m / 1000:.1f} km" if route.distance_m >= 1000 else f"{route.distance_m} m"
        traffic = " with live traffic" if route.traffic_aware else ""
        estimate = f"About {minutes} min by {MODE_LABELS[route.mode]}, {distance}{traffic}."
        return f"{TWO_WHEELER_FALLBACK} {estimate}" if route.fell_back else estimate

    @tool
    async def search_places(query: str, nearby: bool = False) -> str:
        """Find places and their opening hours. Set nearby for requests near the user's current position.

        Use a specific query with a city, address, or neighborhood when the user provides one.
        Returns names, addresses, whether they're open now, regular opening hours and reusable place IDs.
        """
        if not get_settings().google_maps_api_key:
            return MAPS_UNAVAILABLE
        query = _query(query)
        if not query:
            return "A place search query is needed."
        point = None
        if nearby:
            result = await position()
            if result.point is None:
                return result.text
            point = result.point
        try:
            places = await asyncio.to_thread(google_maps.search, query, near=point, hours=True)
        except google_maps.MapsUnavailable:
            return MAPS_UNAVAILABLE
        if not places:
            return "No matching places found."
        public_results = []
        for place in places:
            row = {"name": _display(place.name), "address": _display(place.address),
                   "open_now": place.open_now, "opening_hours": [_display(day) for day in place.hours]}
            if place.place_id:
                found[place.place_id] = place
                row["place_id"] = place.place_id
            if point is not None:
                row["approx_distance_m"] = google_maps.distance_m(point, place.point)
            public_results.append(row)
        return json.dumps(public_results, ensure_ascii=False)

    @tool
    async def where_am_i() -> str:
        """Use the phone's position once to name the user's current street or area when they ask where they are."""
        if not get_settings().google_maps_api_key:
            return MAPS_UNAVAILABLE
        result = await position()
        if result.point is None:
            return result.text
        try:
            address = await asyncio.to_thread(google_maps.address_at, result.point)
        except google_maps.MapsUnavailable:
            return MAPS_UNAVAILABLE
        return _display(address) if address else "Maps couldn't identify a street or area there."

    return [phone_action, get_route, search_places, where_am_i]
