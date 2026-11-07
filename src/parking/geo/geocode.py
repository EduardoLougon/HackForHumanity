"""San Diego Geocoding Service.

Provides place-name-to-coordinate resolution biased to San Diego with
in-memory caching and fallback coordinates for popular landmarks.
"""

import logging
from typing import Dict, Optional, Tuple
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# San Diego bounding box (min_lat, min_lon, max_lat, max_lon)
SD_BBOX = (32.5, -117.6, 33.3, -116.8)

# Fast in-memory cache for common San Diego places
POPULAR_LANDMARKS: Dict[str, Tuple[float, float]] = {
    "petco park": (32.7073, -117.1566),
    "petco": (32.7073, -117.1566),
    "gaslamp": (32.7114, -117.1599),
    "gaslamp quarter": (32.7114, -117.1599),
    "balboa park": (32.7341, -117.1445),
    "san diego zoo": (32.7353, -117.1490),
    "little italy": (32.7214, -117.1687),
    "convention center": (32.7064, -117.1614),
    "san diego convention center": (32.7064, -117.1614),
    "hillcrest": (32.7503, -117.1611),
    "waterfront park": (32.7208, -117.1728),
    "east village": (32.7128, -117.1500),
    "bankers hill": (32.7297, -117.1608),
    "coronado ferry landing": (32.6983, -117.1706),
}

_CACHE: Dict[str, Optional[Tuple[float, float]]] = {}


def geocode_place(query: str) -> Optional[Tuple[float, float]]:
    """Convert a place name or address into (latitude, longitude) inside San Diego.
    Returns None if not found or outside San Diego.
    """
    clean_q = query.strip().lower()
    if clean_q in _CACHE:
        return _CACHE[clean_q]

    # Check popular landmarks dictionary
    if clean_q in POPULAR_LANDMARKS:
        coords = POPULAR_LANDMARKS[clean_q]
        _CACHE[clean_q] = coords
        return coords

    for landmark, coords in POPULAR_LANDMARKS.items():
        if landmark in clean_q or clean_q in landmark:
            _CACHE[clean_q] = coords
            return coords

    # Call Nominatim (OpenStreetMap) with San Diego viewbox restriction
    headers = {
        "User-Agent": "SanDiegoParkingFinder/1.0 (contact: support@sandiego-parking.local)"
    }
    params = {
        "q": f"{query}, San Diego, CA",
        "format": "json",
        "limit": 1,
        "viewbox": "-117.6,33.3,-116.8,32.5",  # left,top,right,bottom
        "bounded": 1,
    }

    try:
        with httpx.Client(timeout=10.0, headers=headers) as client:
            resp = client.get("https://nominatim.openstreetmap.org/search", params=params)
            resp.raise_for_status()
            data = resp.json()
            if data:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                if SD_BBOX[0] <= lat <= SD_BBOX[2] and SD_BBOX[1] <= lon <= SD_BBOX[3]:
                    _CACHE[clean_q] = (lat, lon)
                    return (lat, lon)
    except Exception as e:
        logger.warning(f"Nominatim geocoding failed for '{query}': {e}")

    _CACHE[clean_q] = None
    return None

