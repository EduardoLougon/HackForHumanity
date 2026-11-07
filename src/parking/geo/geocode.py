from threading import Lock

import httpx
from cachetools import TTLCache

from parking.llm.tools import Coordinates


class ServiceUnavailable(Exception):
    pass


class GoogleGeocoder:
    def __init__(self, client: httpx.Client, key: str):
        self.client = client
        self.key = key
        self.cache = TTLCache(maxsize=512, ttl=3600)
        self.lock = Lock()

    def lookup(self, query: str) -> Coordinates | None:
        query = query.strip()
        with self.lock:
            if query.casefold() in self.cache:
                return self.cache[query.casefold()]
        if not self.key:
            raise ServiceUnavailable("Google Geocoding is unconfigured.")
        response = self.client.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={
                "address": query,
                "bounds": "32.5,-117.6|33.3,-116.8",
                "key": self.key,
            },
        )
        response.raise_for_status()
        data = response.json()
        point = None
        if data["status"] == "OK":
            for result in data["results"]:
                city = any(
                    "locality" in component["types"]
                    and component["long_name"] == "San Diego"
                    for component in result["address_components"]
                )
                if city and not result.get("partial_match"):
                    location = result["geometry"]["location"]
                    point = Coordinates(lat=location["lat"], lon=location["lng"])
                    break
        elif data["status"] != "ZERO_RESULTS":
            raise ServiceUnavailable("Google Geocoding is unavailable.")
        with self.lock:
            self.cache[query.casefold()] = point
        return point
