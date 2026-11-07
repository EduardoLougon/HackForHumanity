"""Build the serving snapshot from normalized M1/M2 exports."""

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from parking.serve.store import Store


def build(meters: Path, profiles: Path, metadata: Path, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as directory:
        staged = Path(directory) / "parking.duckdb"
        with duckdb.connect(str(staged)) as db:
            for table, source in (
                ("meters", meters),
                ("availability_profile", profiles),
                ("metadata", metadata),
            ):
                db.execute(
                    f"CREATE TABLE {table} AS SELECT * FROM read_parquet(?)",
                    [str(source)],
                )
            db.execute("CREATE UNIQUE INDEX meter_id ON meters(pole_id)")
            db.execute(
                "CREATE UNIQUE INDEX profile_key ON availability_profile(pole_id, dow, bucket)"
            )
        Store(staged)  # Check the same contract used by API startup before publishing.
        os.replace(staged, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("meters", "profiles", "metadata"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("data/serving/parking.duckdb")
    )
    build(**vars(parser.parse_args()))
