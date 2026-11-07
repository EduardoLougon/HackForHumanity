import httpx

years = [2021, 2022, 2023, 2024, 2025, 2026]
for y in years:
    url = f"https://seshat.datasd.org/parking_meters_transactions/treas_parking_payments_{y}_datasd.csv"
    r = httpx.head(url)
    cl = int(r.headers.get("content-length", 0))
    lm = r.headers.get("last-modified", "unknown")
    print(f"Year {y}: size = {cl / (1024*1024):.1f} MB ({cl} bytes), Last-Modified = {lm}")

import re
with httpx.Client() as client:
    r = client.get("https://data.sandiego.gov/datasets/parking-meters-locations/")
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', r.text):
        if any(w in text.lower() or w in href.lower() for w in ['license', 'terms', 'open']):
            print(f"License / Terms link: {text.strip()} -> {href}")

with httpx.Client() as client:
    r = client.get("https://data.sandiego.gov/datasets/parking-meters-locations/")
    match = re.search(r'License[\s\S]*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', r.text)
    if match:
        print(f"Dataset License: {match.group(2).strip()} -> {match.group(1)}")
