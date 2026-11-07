"""Read the section 7 serving tables once, releasing the source file after startup."""

import json
from pathlib import Path

import duckdb
import numpy as np


def records(connection, sql):
    cursor = connection.execute(sql)
    columns = [column[0] for column in cursor.description]
    while rows := cursor.fetchmany(4096):
        for row in rows:
            yield dict(zip(columns, row))


class Store:
    def __init__(self, path: Path):
        self.profile_ranges = {}
        with duckdb.connect(str(path), read_only=True) as db:
            self.meters = list(
                records(
                    db,
                    """
                SELECT pole_id, lat::DOUBLE AS lat, lon::DOUBLE AS lon, zone, area, sub_area, meter_type,
                       rate_per_hour::DOUBLE AS rate_per_hour, max_minutes, enforced_hours_json, status FROM meters
            """,
                )
            )
            for meter in self.meters:
                hours = meter["enforced_hours_json"]
                if isinstance(hours, str):
                    meter["enforced_hours_json"] = json.loads(hours)
            (metadata,) = records(
                db,
                """SELECT data_as_of, built_at, profile_window_start,
                                    profile_window_end, pipeline_version FROM metadata""",
            )
            self.metadata = dict(metadata, meter_count=len(self.meters))
            if "bucket_minutes" in {
                row[0] for row in db.execute("DESCRIBE metadata").fetchall()
            }:
                self.bucket_minutes = db.execute(
                    "SELECT bucket_minutes FROM metadata"
                ).fetchone()[0]
            else:
                self.bucket_minutes = 15
            if (
                not isinstance(self.bucket_minutes, int)
                or self.bucket_minutes < 1
                or 1440 % self.bucket_minutes
            ):
                raise ValueError("Invalid metadata.bucket_minutes")
            # Keep weekly profiles columnar; only materialize rows needed by a request.
            offset = 0
            for pole, count in db.execute("""
                    SELECT pole_id, count(*) FROM availability_profile GROUP BY pole_id ORDER BY pole_id
                """).fetchall():
                self.profile_ranges[pole] = (offset, count)
                offset += count
            self.profiles = db.sql("""
                SELECT dow, bucket, p_available::DOUBLE AS p_available, band, confidence, basis, n_dates
                FROM availability_profile ORDER BY pole_id, dow, bucket
            """).to_arrow_table()
            self.buckets_per_day = 1440 // self.bucket_minutes
            self.slots = (
                self.profiles["dow"].to_numpy().astype(np.int32) * self.buckets_per_day
                + self.profiles["bucket"].to_numpy()
            )
        self.by_id = {meter["pole_id"]: meter for meter in self.meters}
        self.lat = np.radians([meter["lat"] for meter in self.meters])
        self.lon = np.radians([meter["lon"] for meter in self.meters])

    def nearby(self, lat, lon, radius):
        lat, lon = np.radians([lat, lon])
        a = np.sin((self.lat - lat) / 2) ** 2
        a += np.cos(lat) * np.cos(self.lat) * np.sin((self.lon - lon) / 2) ** 2
        distances = 12742000 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        for i in np.flatnonzero(distances <= radius):
            yield self.meters[i], float(distances[i])

    def profile(self, pole, dow, bucket):
        offset, length = self.profile_ranges.get(pole, (0, 0))
        slots = self.slots[offset : offset + length]
        slot = dow * self.buckets_per_day + bucket
        i = slots.searchsorted(slot)
        if i < length and slots[i] == slot:
            return {
                key: self.profiles[key][offset + int(i)].as_py()
                for key in AVAILABILITY_FIELDS
            }
        return None

    def details(self, pole):
        offset, length = self.profile_ranges.get(pole, (0, 0))
        return dict(
            self.by_id[pole],
            weekly_profile=self.profiles.slice(offset, length).to_pylist(),
        )


AVAILABILITY_FIELDS = ("p_available", "band", "confidence", "basis")
