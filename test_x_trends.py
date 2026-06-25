"""Test X trends free scraper implementation."""
import sys
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.chdir(_HERE)
os.makedirs("output", exist_ok=True)

# Force UTF-8 output on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services.topic_service import _scrape_trends24, _scrape_getdaytrends, fetch_x_trends  # noqa: E402

print("=" * 60)
print("Test 1: trends24.in scraper")
print("=" * 60)
t24 = _scrape_trends24()
print(f"Returned {len(t24)} topics")
for i, t in enumerate(t24[:10], 1):
    print(f"  {i:2}. {t}")

print()
print("=" * 60)
print("Test 2: getdaytrends.com scraper")
print("=" * 60)
gdt = _scrape_getdaytrends()
print(f"Returned {len(gdt)} topics")
for i, t in enumerate(gdt[:10], 1):
    print(f"  {i:2}. {t}")

print()
print("=" * 60)
print("Test 3: fetch_x_trends() combined")
print("=" * 60)
combined = fetch_x_trends()
print(f"Returned {len(combined)} combined unique topics")
for i, t in enumerate(combined[:15], 1):
    print(f"  {i:2}. {t}")

assert len(combined) > 0, "Expected at least some topics!"
print("\nAll tests PASSED.")
