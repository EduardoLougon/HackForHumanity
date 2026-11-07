import time
import httpx
from pathlib import Path

url = "https://seshat.datasd.org/parking_meters_transactions/treas_parking_payments_2026_datasd.csv"
dest = Path("data/raw/transactions/treas_parking_payments_2026_datasd.csv")
dest.parent.mkdir(parents=True, exist_ok=True)

if dest.exists() and dest.stat().st_size > 700 * 1024 * 1024:
    print(f"{dest} already exists ({dest.stat().st_size / (1024*1024):.1f} MB)")
else:
    print(f"Starting download of {url}...")
    t0 = time.time()
    last_print = t0
    downloaded = 0

    with httpx.Client(timeout=600.0) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total_size = int(resp.headers.get("content-length", 0))
            print(f"Content Length: {total_size / (1024*1024):.1f} MB")
            
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_print > 3.0:
                        speed = downloaded / (1024 * 1024 * (now - t0))
                        pct = (downloaded / total_size) * 100 if total_size else 0
                        print(f"Downloaded: {downloaded / (1024*1024):.1f} MB / {total_size / (1024*1024):.1f} MB ({pct:.1f}%) at {speed:.2f} MB/s", flush=True)
                        last_print = now

    print(f"Download complete: {dest.stat().st_size / (1024*1024):.1f} MB in {time.time() - t0:.1f}s", flush=True)

