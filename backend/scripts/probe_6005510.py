import httpx

BASE = "http://127.0.0.1:8000"
tok = httpx.post(f"{BASE}/api/auth/login", data={"username": "admin", "password": "admin123"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
items = httpx.get(f"{BASE}/api/items", headers=H, params={"q": "6005510"}).json()
print("items", len(items))
if items:
    det = httpx.get(f"{BASE}/api/items/{items[0]['id']}", headers=H).json()
    for o in det.get("operations", []):
        print(o.get("seq"), o.get("work_center_code"), o.get("work_center_id"), o.get("cycle_time_sec"), o.get("setup_time_min"))
wcs = httpx.get(f"{BASE}/api/workcenters", headers=H).json()
for w in wcs:
    if "PRESHANE" in w["code"] or w["code"] == "MERGE-DEMO":
        print("WC", w["id"], w["code"], "planned", w.get("is_planned"))
