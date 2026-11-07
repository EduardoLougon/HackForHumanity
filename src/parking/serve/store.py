"""Serving Data Store and Spatial Index.

Loads meters into memory, builds a scikit-learn BallTree (haversine) spatial index,
caches active meter metadata, and manages read-only queries against parking.duckdb.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import duckdb
import numpy as np
from sklearn.neighbors import BallTree
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EARTH_RADIUS_M = 6371000.0
SERVING_DB = Path("data/serving/parking.duckdb")
CONFIG_PATH = Path("config.yaml")


class ParkingStore:
    def __init__(self, db_path: Path = SERVING_DB, config_path: Path = CONFIG_PATH):
        self.db_path = db_path
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self.metadata: Dict[str, Any] = {}
        self.meters_by_id: Dict[str, Dict[str, Any]] = {}
        self.meter_poles: List[str] = []
        self.meter_rows: List[Dict[str, Any]] = []
        self.ball_tree: Optional[BallTree] = None
        self.con: Optional[duckdb.DuckDBPyConnection] = None
        self._last_mtime: float = 0.0

    def load(self):
        """Load serving database and initialize spatial index."""
        if not self.db_path.exists():
            raise FileNotFoundError(f"Serving database not found at {self.db_path}. Run build_serving first.")

        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self._last_mtime = os.path.getmtime(self.db_path)
        logger.info(f"Connecting to serving database at {self.db_path} (read-only)...")
        self.con = duckdb.connect(self.db_path.as_posix(), read_only=True)

        # 1. Load metadata
        meta_row = self.con.execute("SELECT * FROM metadata LIMIT 1").fetchone()
        meta_cols = [c[0] for c in self.con.description]
        self.metadata = dict(zip(meta_cols, meta_row)) if meta_row else {}
        logger.info(f"Loaded metadata: data_as_of={self.metadata.get('data_as_of')}, meters={self.metadata.get('meter_count')}")

        # 2. Load meters
        meter_rows = self.con.execute("SELECT * FROM meters").fetchall()
        cols = [c[0] for c in self.con.description]

        self.meters_by_id = {}
        self.meter_poles = []
        self.meter_rows = []
        coords_deg = []

        for row in meter_rows:
            d = dict(zip(cols, row))
            if d.get("enforced_hours_json"):
                try:
                    d["enforced_hours"] = json.loads(d["enforced_hours_json"])
                except Exception:
                    d["enforced_hours"] = None
            else:
                d["enforced_hours"] = None

            pole_id = d["pole_id"]
            self.meters_by_id[pole_id] = d
            self.meter_poles.append(pole_id)
            self.meter_rows.append(d)
            coords_deg.append([d["lat"], d["lon"]])

        # 3. Build spatial BallTree index (lat, lon in radians)
        if coords_deg:
            coords_rad = np.radians(coords_deg)
            self.ball_tree = BallTree(coords_rad, metric="haversine")
            logger.info(f"Built spatial BallTree index for {len(coords_deg):,} meter poles.")

    def reload_if_changed(self):
        """Hot reload database if file on disk was replaced/updated."""
        if self.db_path.exists():
            mtime = os.path.getmtime(self.db_path)
            if mtime > self._last_mtime:
                logger.info("Serving database file changed on disk. Reloading...")
                self.load()

    def query_radius(self, lat: float, lon: float, radius_m: float) -> List[Tuple[Dict[str, Any], float]]:
        """Query meters within radius_m using BallTree.
        Returns list of (meter_dict, distance_m) sorted by distance.
        """
        self.reload_if_changed()
        if not self.ball_tree or not self.meter_rows:
            return []

        radius_rad = radius_m / EARTH_RADIUS_M
        point_rad = np.radians([[lat, lon]])

        indices, distances = self.ball_tree.query_radius(point_rad, r=radius_rad, return_distance=True)
        idx_list = indices[0]
        dist_rad = distances[0]

        results = []
        for i, d_rad in zip(idx_list, dist_rad):
            meter = self.meter_rows[i]
            dist_m = float(d_rad * EARTH_RADIUS_M)
            results.append((meter, dist_m))

        results.sort(key=lambda x: x[1])
        return results

    def get_profile(self, pole_id: str, dow: int, bucket: int) -> Optional[Dict[str, Any]]:
        """Fetch precomputed availability profile for a single meter bucket."""
        self.reload_if_changed()
        if not self.con:
            return None
        row = self.con.execute("""
            SELECT p_available, band, confidence, basis, n_dates, mean_occupancy, std_occupancy
            FROM availability_profile
            WHERE pole_id = ? AND dow = ? AND bucket = ?
        """, [pole_id, dow, bucket]).fetchone()

        if not row:
            return None
        return {
            "p_available": float(row[0]),
            "band": row[1],
            "confidence": row[2],
            "basis": row[3],
            "n_dates": row[4],
            "mean_occupancy": float(row[5]),
            "std_occupancy": float(row[6]),
        }

    def get_profiles_batch(self, pole_ids: List[str], dow: int, bucket: int) -> Dict[str, Dict[str, Any]]:
        """Fetch precomputed availability profiles for multiple pole_ids at (dow, bucket)."""
        self.reload_if_changed()
        if not self.con or not pole_ids:
            return {}

        placeholders = ", ".join("?" for _ in pole_ids)
        params = [dow, bucket] + pole_ids
        rows = self.con.execute(f"""
            SELECT pole_id, p_available, band, confidence, basis, n_dates, mean_occupancy, std_occupancy
            FROM availability_profile
            WHERE dow = ? AND bucket = ? AND pole_id IN ({placeholders})
        """, params).fetchall()

        result = {}
        for r in rows:
            result[r[0]] = {
                "p_available": float(r[1]),
                "band": r[2],
                "confidence": r[3],
                "basis": r[4],
                "n_dates": r[5],
                "mean_occupancy": float(r[6]),
                "std_occupancy": float(r[7]),
            }
        return result

    def get_meter_details(self, pole_id: str) -> Optional[Dict[str, Any]]:
        """Fetch meter metadata and weekly hourly profile for detail view."""
        self.reload_if_changed()
        meter = self.meters_by_id.get(pole_id)
        if not meter:
            return None

        # Fetch weekly availability pattern
        profiles = self.con.execute("""
            SELECT dow, bucket, p_available, band, confidence, mean_occupancy
            FROM availability_profile
            WHERE pole_id = ?
            ORDER BY dow, bucket
        """, [pole_id]).fetchall()

        weekly_pattern = [
            {
                "dow": p[0],
                "bucket": p[1],
                "p_available": float(p[2]),
                "band": p[3],
                "confidence": p[4],
                "mean_occupancy": float(p[5]),
            }
            for p in profiles
        ]

        return {
            "meter": meter,
            "weekly_profile": weekly_pattern,
            "data_as_of": self.metadata.get("data_as_of"),
        }


# Global store singleton
store = ParkingStore()

