# Composer remediation — durum kaydı

Bu dosya [Kapasite Planlama Analizi](C:/Users/ozan.deniz/Desktop/Kapasite_Planlama_Analizi/Analiz.md) (11 Eylül 2026) ve `Composer_Promptlari` fazlarına göre ilerlemeyi izler. **Kaynak kod kanıttır;** analiz metninden davranış türetilmez.

## Başlangıç snapshot (FAZ 00)

| Alan | Değer |
|---|---|
| **HEAD** | `58b7219185d30c91cbd7292c893d3faf77af699b` |
| **Commit mesajı** | Toplu plan revizyonu ve siparis stok kodu filtresi. |
| **Analiz tabanı** | Aynı commit (`58b7219`) — HEAD ile analiz hizalı |
| **Working tree** | Temiz (FAZ 00 öncesi) |
| **Backend test** | `72 passed` in `33.40s` (`backend\.venv\Scripts\python.exe -m pytest tests/ -q`) |
| **Frontend build** | Başarılı (`npm run build`, Vite 5.4.21, ~2.4s) |
| **Canlı DB** | Dokunulmadı; seed/drop_all/reset yapılmadı |

### Test ortamı vs canlı PostgreSQL

| | Test (`backend/tests/conftest.py`) | Canlı / geliştirme |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./test_kapasite.db` (import öncesi `os.environ` ile set) | `backend/.env` → PostgreSQL (`postgresql+psycopg://…`) |
| Şema | Session fixture: `drop_all` + `create_all` | `create_all` + `ensure_columns` + `widen_revision_change_values` |
| Temizlik | Test sonunda `test_kapasite.db` silinir | — |
| İzolasyon | **Doğrulandı** — pytest canlı PG'ye bağlanmaz | Portable PG: `backend/.postgres/` (gitignore) |

`verify_findings.py` (analiz klasörü): kendi sürecinde `sqlite:///:memory:` kullanır; uygulama açılış rutinini çalıştırmaz. FAZ 00'da **çalıştırılmadı** (ortam incelendi). `verification-results.json` B1–B7 hatalarını yeniden üretir; istenen davranış değildir.

---

## B1–B7 — kod konumu, doğrulama, faz eşlemesi

Durum kodları: **doğrulanmış** = analiz betiği/inceleme ile hâlâ repro; **kısmi** = yalnızca alt akışta düzeltilmiş; **bekliyor** = FAZ 01+ ile düzeltilecek.

### B1 · Otomatik plan tamamlanan üretimi yeniden yüklüyor (P0)

| | |
|---|---|
| **Durum** | **doğrulanmış** (genel `simulate` / `auto_plan`); **kısmi** (`job_moves.remaining_chain` üretim ilerlemesini okur) |
| **Kod** | `backend/app/services/planning.py` — `_place_order` → `_place_quantity(..., o.quantity, ...)` (~L221); `simulate()` üretim ilerlemesini düşmez |
| **Kısmi düzeltme** | `backend/app/services/job_moves.py` — `remaining_chain()` → `wip._produced_qty_map()` |
| **Analiz kanıtı** | `verification-results.json`: 6/10 üretilmiş → `observed_planned_hours: 10.0`, beklenen 4 |
| **FAZ** | **02** — `remaining_work.py` + tüm plan motorları |
| **Regresyon testi (henüz eklenmedi)** | Modül: `tests/test_remaining_work.py` (FAZ 02'de oluşturulacak). Fixture: in-memory SQLite veya mevcut `_upload` + `ProductionActual` import (`test_capacity_flow._upload`). Senaryo: 10 adet sipariş, op seq 10'da 6 adet `ProductionActual`, `simulate(start_week=…, weeks=1)` → `planned_hours == 4`, `planned_qty` toplamı 4. Aynı senaryo `auto_plan(replace_existing=True)` ve `due_date` / `revenue` modları. **xfail yok.** |

### B2 · Ön operasyon plansızken sonraki planlanıyor (P0)

| | |
|---|---|
| **Durum** | **doğrulanmış** |
| **Kod** | `backend/app/services/planning.py` — `_place_quantity` (~L120–L124): `prev_op` yalnızca hafta indeksi taşır; önceki adım `unplanned` olsa bile sonraki adım `min_start_idx` ile devam eder |
| **Analiz kanıtı** | `observed_downstream_qty: 10.0`, beklenen 0 |
| **FAZ** | **04** — öncül miktar / WIP |
| **Regresyon testi (henüz eklenmedi)** | Modül: `tests/test_plan_precedence.py`. İki operasyonlu rota; op1 WC kapasitesi 0, op2 kapasitesi 40 saat. `_place_quantity` veya tam `simulate` → op2 `planned_qty == 0`. WIP=3 senaryosu ayrı test. |

### B3 · Geçiş kuralları otomatik planda uygulanmıyor (P0)

| | |
|---|---|
| **Durum** | **doğrulanmış** |
| **Kod** | `backend/app/services/planning.py` — `_place_quantity` (~L123–L124): `RuleLookup.get` → yalnızca `finish` / `cycles`; `wait_minutes` / `lag_cycles` yok. `lead_time` ayrı yol: `_schedule_op` (~L633+) |
| **Analiz kanıtı** | 14 gün bekleme → her iki op aynı hafta `2026-09-14` |
| **FAZ** | **04** — ortak kural motoru |
| **Regresyon testi (henüz eklenmedi)** | Modül: `tests/test_transition_rules_planning.py`. `OpTransitionRule` + `wait_minutes=20160` (14 gün); iki op, 3 haftalık ufuk → ikinci op ilk haftada **olmamalı**. `cycles` + `lag_cycles` ayrı parametreli test. |

### B4 · Sıfır kapasitede uydurma termin (P0)

| | |
|---|---|
| **Durum** | **doğrulanmış** |
| **Kod** | `backend/app/services/planning.py` — `_schedule_op` (~L644–L650): `guard < 730` ile hafta atlar; sıfır kapasitede `2040-09-10` döner |
| **Analiz kanıtı** | `observed_start/end: 2040-09-10 08:00:00` |
| **FAZ** | **05** — açık başarısızlık + ekran birliği |
| **Regresyon testi (henüz eklenmedi)** | Modül: `tests/test_leadtime_infeasible.py`. WC `headcount=0`; `lead_time` API veya `_schedule_op` doğrudan → `success=False` veya `plan_status=infeasible`, bitiş `null`, kalan saat 10. 15 dk'lık op ayrı alt test (B4 alt durumu). |

### B5 · Ekleme modunda mükerrer plan (P1)

| | |
|---|---|
| **Durum** | **doğrulanmış** |
| **Kod** | `backend/app/services/planning.py` — `simulate` (~L282–L284): mevcut auto plan kapasiteden düşülür; `remaining_work` yok. `auto_plan` `replace_existing=False` ile iki kez → 20 saat |
| **Analiz kanıtı** | `observed_total_hours: 20.0`, gereksinim 10 |
| **Not** | UI varsayılan `replace_existing=True`; API/servis seçeneği hâlâ riskli |
| **FAZ** | **02** |
| **Regresyon testi (henüz eklenmedi)** | Modül: `tests/test_remaining_work.py` veya `tests/test_plan_append_mode.py`. `AutoPlanRequest(replace_existing=False)` ×2 → toplam plan saati 10. Manuel 4 saat + auto → max 6 saat ek yük. |

### B6 · Partiler tekil siparişlerden önce (P1)

| | |
|---|---|
| **Durum** | **doğrulanmış** |
| **Kod** | `backend/app/services/planning.py` — `simulate` due_date dalı (~L350–L361): önce `batches` döngüsü, sonra `orders`; ayrı sıralama |
| **Analiz kanıtı** | Acil tekil plansız; geç terminli parti (`LATER`) yerleşti |
| **FAZ** | **03** |
| **Regresyon testi (henüz eklenmedi)** | Modül: `tests/test_batch_priority.py`. 40 saat kapasite; erken termin tekil + geç termin parti → tekil planlanır, parti plansız veya kısmi. Ciro modu ayrı test. |

### B7 · Yeniden planlama ufuk dışını siliyor (P0)

| | |
|---|---|
| **Durum** | **doğrulanmış** |
| **Kod** | `backend/app/services/planning.py` — `write_simulation` / `auto_plan` (~L397–L401): `week_start >= sim.start`, **üst sınır yok** |
| **Analiz kanıtı** | Kasım satırı silindi (`observed_future_line_count: 0`) |
| **FAZ** | **01** |
| **Regresyon testi (henüz eklenmedi)** | Modül: `tests/test_plan_horizon_scope.py`. Kasım `PlanLine` + 1 haftalık Eylül `auto_plan(replace_existing=True)` → Kasım satırı kalır. Üst sınır haftası (start+weeks) korunur testi. Revizyon `approve` aynı kapsam testi (FAZ 01 sonrası). |

---

## Faz tamamlanma tablosu (01–12)

| Faz | Konu | Bulgu / kapsam | Durum | Commit |
|---|---|---|---|---|
| **00** | Başlangıç snapshot + regresyon planı | — | **tamamlandı** | `a0d879b` |
| **01** | Ufuk dışı silmeyi durdur | B7 | bekliyor | — |
| **02** | Tek kalan iş; mükerrer plan | B1, B5 | bekliyor | — |
| **03** | Parti + tekil aynı sıra | B6 | bekliyor | — |
| **04** | Öncül + geçiş kuralları | B2, B3 | bekliyor | — |
| **05** | Termin başarısızlığı açık | B4, M3 (gösterim) | bekliyor | — |
| **06** | Revizyon onay = hesaplanan plan | T1 | bekliyor | — |
| **07** | Stok netleştirme + malzeme hazır | M2 | bekliyor | — |
| **08** | Ölçüm / KPI birimleri | M5 | bekliyor | — |
| **09** | Tam yedek + şema bütünlüğü | T2 | bekliyor | — |
| **10** | İnsan–makine + günlük takvim altyapısı | M1 | bekliyor | — |
| **11** | Pilot günlük çizelge + parti hazırlığı | M3, M4 | bekliyor | — |
| **12** | Kabul ve ölçüm özeti | T3, tüm fazlar | bekliyor | — |

Prompt dosyaları: `C:\Users\ozan.deniz\Desktop\Kapasite_Planlama_Analizi\Composer_Promptlari\Faz_XX.md`

---

## FAZ 00 kabul ölçütleri

| # | Ölçüt | Sonuç |
|---|---|---|
| 1 | Mevcut test + frontend build kayıtlı | Evet (yukarıdaki tablo) |
| 2 | B1–B7 kod konumu + faz eşlemesi | Evet (yukarıdaki bölüm) |
| 3 | İş davranışı değişmedi | Evet — yalnızca dokümantasyon |

---

## Bilinen sınırlar (FAZ 00)

- Regresyon testleri **henüz suite'e eklenmedi** (bilinçli; failing test / xfail yok).
- `verify_findings.py` bu fazda çalıştırılmadı; sonuçlar `verification-results.json` dosyasından alındı.
- Testler SQLite üzerinde; PostgreSQL eşzamanlılık davranışı FAZ 09/T3 kapsamında.
- Analizdeki 72 test sayısı güncel HEAD ile örtüşüyor; ileride test eklendikçe sayı artacak.

---

## Sonraki adım

**FAZ 01** — `Composer_Promptlari/Faz_01.md` (B7: plan silme kapsamını ufupla sınırla). Kullanıcı promptu verdiğinde uygulanır.
