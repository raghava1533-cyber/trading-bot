import json

with open("instruments_cache.json", "r") as f:
    data = json.load(f)

# Print unique segments
segments = set(item.get("segment") for item in data)
print("Segments found:", segments)

# Print first 5 rows that have "NIFTY" anywhere
nifty_rows = [item for item in data if "NIFTY" in str(item).upper()][:5]
for row in nifty_rows:
    print(row)