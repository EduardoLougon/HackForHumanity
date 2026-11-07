import time
from threading import Lock

import httpx
from cachetools import TTLCache

from parking.llm.tools import Coordinates


class ServiceUnavailable(Exception):
    pass


class NominatimGeocoder:
    def __init__(
        self,
        client: httpx.Client,
        endpoint: str = "https://nominatim.openstreetmap.org/search",
    ):
        self.client = client
        self.endpoint = endpoint
        self.cache = TTLCache(maxsize=512, ttl=3600)
        self.lock = Lock()
        self.next_request = 0.0

    def lookup(self, query: str) -> Coordinates | None:
        query = query.strip()
        with self.lock:
            if query.casefold() in self.cache:
                return self.cache[query.casefold()]
            delay = self.next_request - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_request = time.monotonic() + 1.0
            response = self.client.get(
                self.endpoint,
                headers={
                    "User-Agent": "HackForHumanity-Parking/0.1 (+https://github.com/EduardoLougon/HackForHumanity)"
                },
                params={
                    "q": query,
                    "viewbox": "-117.6,33.3,-116.8,32.5",
                    "bounded": 1,
                    "countrycodes": "us",
                    "format": "geocodejson",
                    "addressdetails": 1,
                    "limit": 5,
                    "accept-language": "en",
                },
            )
            response.raise_for_status()
            try:
                features = response.json()["features"]
                point = None
                for feature in features:
                    props = feature["properties"]["geocoding"]
                    if props.get("city") != "San Diego":
                        continue
                    lon, lat = feature["geometry"]["coordinates"]
                    point = Coordinates(lat=lat, lon=lon)
                    break
            except (KeyError, TypeError, ValueError) as exc:
                raise ServiceUnavailable("Invalid response from place search.") from exc
            self.cache[query.casefold()] = point
            return point
