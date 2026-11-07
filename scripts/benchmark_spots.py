"""Latency Benchmark for /v1/spots endpoint."""

import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from fastapi.testclient import TestClient

from src.parking.api.main import app
from src.parking.serve.store import store

store.load()
client = TestClient(app)

lat, lon = 32.7073, -117.1566  # Petco Park
latencies = []

# Warm-up
for _ in range(5):
    client.get(f"/v1/spots?lat={lat}&lon={lon}&limit=10")

# 100 requests benchmark
for _ in range(100):
    t0 = time.perf_counter()
    resp = client.get(f"/v1/spots?lat={lat}&lon={lon}&limit=10")
    latencies.append((time.perf_counter() - t0) * 1000.0)

p50 = np.percentile(latencies, 50)
p90 = np.percentile(latencies, 90)
p95 = np.percentile(latencies, 95)
p99 = np.percentile(latencies, 99)

print(f"Results across 100 requests:")
print(f"  p50: {p50:.2f} ms")
print(f"  p90: {p90:.2f} ms")
print(f"  p95: {p95:.2f} ms")
print(f"  p99: {p99:.2f} ms")
assert p95 < 200.0, f"p95 latency ({p95:.2f}ms) exceeded 200ms target!"
print("BENCHMARK PASSED: p95 latency is well under 200 ms!")
