"""E2E-01: Kimlik doğrulama, roller, master veri (Excel + API) ve verimli iş gücü kapasitesi."""
from datetime import timedelta

from tests.e2e.conftest import WEEK, is_xlsx, upload, wc_by_code


def test_auth_and_role_matrix(client, auth, sc):
    """Giriş, oturum bilgisi, yanlış şifre, tokensiz erişim ve kullanıcı rolü yetki matrisi."""
    me = client.get("/api/auth/me", headers=auth)
    sc.step("Admin oturumu /auth/me", me.status_code == 200 and me.json()["role"] == "admin", user=me.json().get("username"))

    bad = client.post("/api/auth/login", data={"username": "admin", "password": "yanlis"})
    sc.step("Yanlış şifre reddedilir", bad.status_code in (400, 401), status=bad.status_code)

    anon = client.get("/api/workcenters")
    sc.step("Tokensiz istek reddedilir", anon.status_code == 401, status=anon.status_code)

    r = client.post("/api/users", headers=auth, json={"username": "e2e_izleyici", "password": "123456", "role": "user"})
    sc.step("Salt okunur kullanıcı oluşturuldu (veya zaten var)", r.status_code in (201, 400, 409), status=r.status_code)
    tok = client.post("/api/auth/login", data={"username": "e2e_izleyici", "password": "123456"}).json()["access_token"]
    ro = {"Authorization": f"Bearer {tok}"}
    sc.step("user rolü okuyabilir", client.get("/api/workcenters", headers=ro).status_code == 200)
    sc.step("user rolü master yazamaz (403)", client.post("/api/workcenters", headers=ro, json={"code": "X", "name": "X"}).status_code == 403)
    sc.step("user rolü kullanıcı listesini göremez (403)", client.get("/api/users", headers=ro).status_code == 403)
    sc.step("user rolü plan çalıştıramaz (403)", client.post("/api/plan/auto", headers=ro, json={"start_week": WEEK.isoformat()}).status_code == 403)

    users = client.get("/api/users", headers=auth).json()
    uid = next(u["id"] for u in users if u["username"] == "e2e_izleyici")
    p = client.patch(f"/api/users/{uid}", headers=auth, json={"is_active": False})
    sc.step("Kullanıcı pasife alındı", p.status_code == 200, status=p.status_code)
    sc.step("Pasif kullanıcı giriş yapamaz", client.post("/api/auth/login", data={"username": "e2e_izleyici", "password": "123456"}).status_code in (400, 401, 403))


def test_master_data_and_weekly_capacity(client, auth, master, sc):
    """İş merkezi/vardiya/haftalık iş gücü → 10×4×5 = 200 saat = 20 birim; haftalık istisna ve Excel gidiş-dönüş."""
    prs = master["prs"]
    sc.step("İş merkezi Excel ile geldi", prs["code"] == "E2E-PRS" and prs["is_planned"] is True, id=prs["id"])

    machines = client.get("/api/machines", headers=auth, params={"work_center_id": prs["id"]}).json()
    sc.step("Makine tanımı bağlandı", any(m["code"] == "E2E-PRS-M1" for m in machines), count=len(machines))

    cap = client.get("/api/capacity", headers=auth, params={"start": WEEK.isoformat()}).json()
    row = next(c for c in cap if c["work_center_code"] == "E2E-PRS")
    sc.step("Haftalık verimli kapasite 200 saat", row["capacity_hours"] == 200, hours=row["capacity_hours"])
    sc.step("Kapasite birimi 20 (birim = 10 saat)", row["capacity_units"] == 20, units=row["capacity_units"])
    sc.step("5 çalışma günü", len(row["days"]) == 5)
    mnt = next(c for c in cap if c["work_center_code"] == "E2E-MNT")
    sc.step("İkinci merkez 100 saat", mnt["capacity_hours"] == 100, hours=mnt["capacity_hours"])

    # Haftalık istisna: 1 hafta 5 kişi → 100 saat; sonra sil → 200
    wk = WEEK + timedelta(weeks=1)
    r = client.put(f"/api/workcenters/{prs['id']}/weeks/{wk.isoformat()}", headers=auth,
                   json={"headcount": 5, "efficient_hours_per_person": 4, "working_days": 5, "note": "e2e izin"})
    sc.step("Haftalık istisna kaydedildi", r.status_code == 200, status=r.status_code)
    cap2 = next(c for c in client.get("/api/capacity", headers=auth, params={"start": wk.isoformat()}).json() if c["work_center_code"] == "E2E-PRS")
    sc.step("İstisna haftasında kapasite 100", cap2["capacity_hours"] == 100, hours=cap2["capacity_hours"])
    weeks = client.get(f"/api/workcenters/{prs['id']}/weeks", headers=auth, params={"start": WEEK.isoformat(), "weeks": 4}).json()
    sc.step("İstisna listede", any(w["week_start"] == wk.isoformat() and w["headcount"] == 5 for w in weeks), count=len(weeks))
    sc.step("Tüm merkezler haftalık görünümü", client.get("/api/wc-weeks", headers=auth, params={"start": WEEK.isoformat(), "weeks": 4}).status_code == 200)
    sc.step("Haftalık iş gücü Excel dışa aktarımı", is_xlsx(client.get("/api/exports/wc-weeks.xlsx", headers=auth, params={"start": WEEK.isoformat(), "weeks": 4})))
    # Excel ile geri yükle: 10 kişi → 200'e döner
    upload(client, auth, "wc_weeks", ["İş Merkezi Kodu", "Hafta", "Kişi Sayısı", "Kişi Başı Verimli Saat", "Çalışma Günü"],
           [["E2E-PRS", wk.isoformat(), 10, 4, 5]])
    cap3 = next(c for c in client.get("/api/capacity", headers=auth, params={"start": wk.isoformat()}).json() if c["work_center_code"] == "E2E-PRS")
    sc.step("Excel gidiş-dönüş sonrası 200 saat", cap3["capacity_hours"] == 200, hours=cap3["capacity_hours"])


def test_workcenter_api_crud_and_reserve(client, auth, sc):
    """API ile iş merkezi/vardiya/makine oluştur-güncelle-sil; planlama rezervi ve pasif merkez davranışı."""
    r = client.post("/api/workcenters", headers=auth, json={"code": "E2E-TMP", "name": "Geçici", "is_planned": True, "capacity_unit_hours": 8, "planning_reserve_pct": 20})
    sc.step("İş merkezi API ile oluşturuldu", r.status_code in (200, 201), status=r.status_code)
    wc = wc_by_code(client, auth, "E2E-TMP")
    s = client.post(f"/api/workcenters/{wc['id']}/shifts", headers=auth, json={"name": "Gece", "weekdays": "0,1,2,3,4", "start_time": "20:00", "end_time": "04:00", "headcount": 3, "efficient_hours_per_person": 5})
    sc.step("Vardiya eklendi", s.status_code in (200, 201), status=s.status_code)
    m = client.post(f"/api/workcenters/{wc['id']}/machines", headers=auth, json={"code": "E2E-TMP-M1", "name": "Geçici Makine"})
    sc.step("Makine eklendi", m.status_code in (200, 201), status=m.status_code)
    mid = m.json()["id"]
    sc.step("Makine güncellendi", client.put(f"/api/machines/{mid}", headers=auth, json={"code": "E2E-TMP-M1", "name": "Yeni Ad", "is_active": False}).status_code == 200)
    upd = client.put(f"/api/workcenters/{wc['id']}", headers=auth, json={**{k: wc[k] for k in ("code", "name")}, "is_planned": False, "capacity_unit_hours": 8})
    sc.step("İş merkezi güncellendi (planlama dışı)", upd.status_code == 200 and upd.json()["is_planned"] is False)
    sc.step("Makine silindi", client.delete(f"/api/machines/{mid}", headers=auth).status_code in (200, 204))
    sc.step("İş merkezi silindi", client.delete(f"/api/workcenters/{wc['id']}", headers=auth).status_code in (200, 204))
    sc.step("Silinen merkez listede yok", all(w["code"] != "E2E-TMP" for w in client.get("/api/workcenters", headers=auth).json()))


def test_reference_endpoints(client, auth, master, sc):
    """Yardımcı uç noktalar: ürün grupları, stok kartı, kaynak modeli istatistiği, takvim istisnaları, veri bütünlüğü."""
    groups = client.get("/api/items/groups", headers=auth).json()
    sc.step("Ana/alt grup listeleri", isinstance(groups.get("main_groups"), list) and isinstance(groups.get("sub_groups"), list), main=len(groups.get("main_groups", [])), sub=len(groups.get("sub_groups", [])))
    sg = client.get("/api/scenarios/groups", headers=auth).json()
    sc.step("Ürün grubu E2EGRP senaryo gruplarında", any(g.get("product_group") == "E2EGRP" for g in sg), count=len(sg))
    items = client.get("/api/items", headers=auth, params={"q": "E2E-MAM"}).json()
    rows = items if isinstance(items, list) else items.get("items", items.get("rows", []))
    sc.step("Stok kartı arama", any(i["code"] == "E2E-MAM" for i in rows), count=len(rows))
    item = next(i for i in rows if i["code"] == "E2E-MAM")
    d = client.get(f"/api/items/{item['id']}", headers=auth)
    sc.step("Stok kartı detayı (rota/BOM)", d.status_code == 200, keys=list(d.json().keys())[:12])
    sc.step("Kaynak modeli istatistiği", client.get("/api/resource-model/stats", headers=auth).status_code == 200)
    sc.step("Takvim istisnaları listesi", client.get("/api/calendar-exceptions", headers=auth).status_code == 200)
    integ = client.get("/api/data-integrity", headers=auth)
    sc.step("Veri bütünlüğü raporu", integ.status_code == 200, keys=list(integ.json().keys())[:10] if isinstance(integ.json(), dict) else len(integ.json()))
