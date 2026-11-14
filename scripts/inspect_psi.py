import requests
from pathlib import Path

URL = "https://psi.nasa.gov/physci/repo/search"
params = {
    "q": "SAFFIRE",
    "data_source": "psi",
    "data_type": "investigation",
}

response = requests.get(
    URL,
    params=params,
    timeout=30,
)

print("Status:", response.status_code)
print("Content type:", response.headers.get("Content-Type"))
print("Final URL:", response.url)
print("Response length:", len(response.text))

output_dir = Path("data/raw/psi")
output_dir.mkdir(parents=True, exist_ok=True)
output_file = output_dir / "saffire_search.html"
output_file.write_text(response.text, encoding="utf-8")

print("Saved to:", output_file)
print("First 1000 characters:")
print(response.text[:1000])
