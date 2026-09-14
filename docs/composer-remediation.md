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
| **Durum** | **düzeltildi** (FAZ 02) |
| **Yeni kod** | `remaining_work.py` → `qty_to_schedule = max(remaining_execution − preserved_planned, 0)`; `planning._place_order` / `_place_batch` |
| **Regresyon** | `tests/test_remaining_work.py::test_completed_production_schedules_remaining_only` |
| **FAZ** | **02** ✓ |

### B2 · Ön operasyon plansızken sonraki planlanıyor (P0)

| | |
|---|---|
| **Durum** | **düzeltildi** (FAZ 04) |
| **Yeni kod** | `operation_constraints.max_successor_qty` + `_place_quantity` oncul zinciri |
| **Regresyon** | `tests/test_transition_rules_planning.py::test_predecessor_zero_capacity_blocks_successor` |
| **FAZ** | **04** ✓ |

### B3 · Geçiş kuralları otomatik planda uygulanmıyor (P0)

| | |
|---|---|
| **Durum** | **düzeltildi** (FAZ 04) |
| **Yeni kod** | `transition_min_week_index`, `wait_minutes` haftalik yuvarlama; `leadtime_earliest_datetime` ortak |
| **Regresyon** | `tests/test_transition_rules_planning.py` (finish bekleme, cycles tetik) |
| **FAZ** | **04** ✓ |

### B4 · Sıfır kapasitede uydurma termin (P0)

| | |
|---|---|
| **Durum** | **düzeltildi** (FAZ 05) |
| **Yeni kod** | `ScheduleOpResult` + tarih ufku; `LeadTimeOut.status` (`complete`/`partial`/`infeasible`); `end=null`; tahmin API/ UI engeli |
| **Regresyon** | `tests/test_leadtime_infeasible.py` (6 test) |
| **FAZ** | **05** ✓ |

### B5 · Ekleme modunda mükerrer plan (P1)

| | |
|---|---|
| **Durum** | **düzeltildi** (FAZ 02) |
| **Yeni kod** | `replace_existing=False` → mevcut auto ufuk içi `preserved_planned_qty` sayılır; ikinci çağrı `qty_to_schedule=0` |
| **Regresyon** | `tests/test_remaining_work.py::test_append_mode_no_duplicate_plan` |
| **FAZ** | **02** ✓ |

### B6 · Partiler tekil siparişlerden önce (P1)

| | |
|---|---|
| **Durum** | **düzeltildi** (FAZ 03) |
| **Eski kod** | `simulate`: once `batches` dongusu, sonra `orders`; ciro modunda partiler her zaman once |
| **Yeni kod** | `planning_candidates.build_planning_candidates` + tek siralama; `_place_candidate` |
| **Regresyon** | `tests/test_batch_priority.py` (5 test) |
| **FAZ** | **03** ✓ |

### B7 · Yeniden planlama ufuk dışını siliyor (P0)

| | |
|---|---|
| **Durum** | **düzeltildi** (FAZ 01) |
| **Eski kod** | `write_simulation`: `week_start >= sim.start` — üst sınır yok → Kasım dahil tüm gelecek auto satırları siliniyordu |
| **Yeni kod** | `plan_horizon_scope` + `delete_lines_in_replace_scope`: `[start, start+weeks×7)` yarı açık aralık; seçili WC; `auto` (+ isteğe `manual`); forecast dokunulmaz |
| **Regresyon** | `tests/test_plan_horizon_scope.py` — 5 test geçti |
| **FAZ** | **01** ✓ |

---

## Faz tamamlanma tablosu (01–12)

| Faz | Konu | Bulgu / kapsam | Durum | Commit |
|---|---|---|---|---|
| **00** | Başlangıç snapshot + regresyon planı | — | **tamamlandı** | `a0d879b` |
| **01** | Ufuk dışı silmeyi durdur | B7 | **tamamlandı** | `f6be5eb` |
| **02** | Tek kalan iş; mükerrer plan | B1, B5 | **tamamlandı** | 662f490 |
| **03** | Parti + tekil aynı sıra | B6 | **tamamlandı** | c0d108c |
| **04** | Öncül + geçiş kuralları | B2, B3 | **tamamlandı** | 1d3d549 |
| **05** | Termin başarısızlığı açık | B4, M3 (gösterim) | **tamamlandı** | (bu commit) |
| **06** | Revizyon onay = hesaplanan plan | T1 | bekliyor | — |
| **07** | Stok netleştirme + malzeme hazır | M2 | bekliyor | — |
| **08** | Ölçüm / KPI birimleri | M5 | bekliyor | — |
| **09** | Tam yedek + şema bütünlüğü | T2 | bekliyor | — |
| **10** | İnsan–makine + günlük takvim altyapısı | M1 | bekliyor | — |
| **11** | Pilot günlük çizelge + parti hazırlığı | M3, M4 | bekliyor | — |
| **12** | Kabul ve ölçüm özeti | T3, tüm fazlar | bekliyor | — |

Prompt dosyaları: `C:\Users\ozan.deniz\Desktop\Kapasite_Planlama_Analizi\Composer_Promptlari\Faz_XX.md`

---

## FAZ 01 — değişen dosyalar ve testler

| Dosya | Değişiklik |
|---|---|
| `backend/app/services/planning.py` | `PlanHorizonScope`, `plan_horizon_scope`, `delete_lines_in_replace_scope`; `write_simulation` silme kapsamı |
| `backend/app/services/plan_preflight.py` | `replace_scope` — tarih, WC, mod, etkilenecek satır sayısı |
| `backend/app/schemas.py` | `PlanPreflightScopeOut` |
| `frontend/src/api.ts` | `PlanPreflightScope` tipi |
| `frontend/src/pages/planning/PlanPreflightModal.tsx` | “Seçili ufku yeniden planla” onay UI |
| `backend/tests/test_plan_horizon_scope.py` | B7 regresyon (5 test) |

### FAZ 01 kabul ölçütleri

| # | Ölçüt | Sonuç |
|---|---|---|
| 1 | 14.09 + 1 hf → 02.11 auto korunur | ✓ `test_replan_preserves_future_auto_line` |
| 2 | Önce / başka WC / manuel / forecast korunur | ✓ `test_replan_preserves_before_other_wc_manual_forecast` |
| 3 | Üst sınır haftası korunur, ufuk içi yenilenir | ✓ `test_replan_upper_bound_week_preserved_inside_replaced` |
| 4 | Revizyon onay aynı kapsam | ✓ `test_revision_apply_same_horizon_scope` |
| 5 | Preflight kapsam raporu | ✓ `test_preflight_reports_replace_scope` |
| 6 | Backend suite | **77 passed** (~52.8s) |
| 7 | Frontend build | Başarılı (Vite 5.4.21) |

**Not:** `DELETE /api/plan/lines` (açık silme) davranışı değiştirilmedi — başlangıçtan sonrasını sınırsız siler.

---

## FAZ 02 — kalan iş formülü ve tüketiciler

**Formül (operasyon / sipariş kimliği):**

```
required_qty          = BOM katsayili miktar (explode_order)
completed_good_qty    = produced_qty_map (order_id + operation_id)
remaining_execution   = max(required − completed_good, 0)
preserved_planned_qty = ufuk disi + (ufuk ici manuel) + (ufuk ici auto, replace_existing=False)
                        gecmis hafta satirlari korunmaz (gecikmis is yeniden planlanir)
qty_to_schedule       = max(remaining_execution − preserved_planned, 0)
setup_required        = completed_good_qty <= 0
```

**Tüketiciler:** `planning.simulate` / `_place_order` / `_place_batch`, `job_moves.remaining_chain`, `requirements.requirement_lines` (`remaining_hours`), `wip._produced_qty_map` (delegasyon).

| Dosya | Değişiklik |
|---|---|
| `backend/app/services/remaining_work.py` | **yeni** — ortak kalan is servisi |
| `backend/app/services/planning.py` | `_place_order`, `_place_batch`, `operation_run_hours`, `setup_by_op` |
| `backend/app/services/job_moves.py` | `required_qty_by_operation` + `produced_qty_map` |
| `backend/app/services/wip.py` | `produced_qty_map` delegasyonu; position_no belirsizligi uyarisi |
| `backend/app/services/requirements.py` | `remaining_hours` alani (brut `hours` korundu) |
| `backend/app/schemas.py` | `RequirementLine.remaining_hours` |
| `backend/tests/test_remaining_work.py` | B1/B5 regresyon (6 test) |
| `backend/tests/conftest.py` | Testler arasi plan/uretim izolasyonu |

### FAZ 02 kabul ölçütleri

| # | Ölçüt | Sonuç |
|---|---|---|
| 1 | 6/10 uretilmis → 4 saat plan | ✓ |
| 2 | Append ×2 → toplam 10 saat | ✓ |
| 3 | Manuel 4 saat → auto 6 saat | ✓ |
| 4 | Ufuk disi plan tekrar eklenmez | ✓ |
| 5 | Tamamlanan op 0; gecmis plansiz yeniden planlanir | ✓ |
| 6 | Parti + BOM katsayisi 2 | ✓ |
| 7 | Backend suite | **82 passed**, 1 flaky (`test_wip_multi_route` full-suite; tek basina gecer) (~123s) |
| 8 | Frontend build | Başarılı |

**Kalan (FAZ 07+):** Bitmis urun rezervasyonu/sevkiyat cift dusme; position_no belirsiz uretimde UI uyarisi.

---

## FAZ 03 — ortak aday listesi ve oncelik politikasi

**Oncelik (termin):** `effective_due_date` artan → `display_code` → `(kind, candidate_id)`

**Oncelik (ciro):** `remaining_sales_value / remaining_required_hours` azalan → termin → kimlik. Sifir saat/fiyatsiz en sona (uyari).

**Parti termin:** bagli siparislerin en erken `effective_due` (parti `due_date` degil).

| Dosya | Degisiklik |
|---|---|
| `backend/app/services/planning_candidates.py` | **yeni** — `PlanningCandidate`, siralama, skipped kaydi |
| `backend/app/services/planning.py` | `simulate` tek aday dongusu; `_place_candidate` |
| `backend/app/services/revenue.py` | etiket: Ciro oncelikli (sezgisel) |
| `frontend/src/pages/Planning.tsx` | mod etiketi; skipped parti gosterimi |
| `frontend/src/pages/planning/ComparePanel.tsx` | karsilastirma metinleri |
| `frontend/src/pages/planning/RevisionsPanel.tsx` | mod etiketi |
| `backend/tests/test_batch_priority.py` | B6 regresyon (5 test) |
| `backend/tests/conftest.py` | testler arasi parti temizligi |
| `backend/tests/test_capacity_flow.py` | WC/siparis izolasyonu (suite kirilmasin) |

### FAZ 03 kabul olcutleri

| # | Olcut | Sonuc |
|---|---|---|
| 1 | Erken termin tekil, gec termin parti (40h) | ✓ |
| 2 | Parti tek basina oncelik yukseltmez | ✓ |
| 3 | Yuksek ciro/saat tekil once | ✓ |
| 4 | Sigmayan parti skipped | ✓ |
| 5 | Deterministik sira (×3) | ✓ |
| 6 | Backend suite | **87 passed**, 1 flaky (`test_wip_multi_route`) (~41s) |
| 7 | Frontend build | Basarili |

**Birlikte sevk:** yalnizca `due_date` modunda ayri policy (degismedi).

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

## FAZ 04 — oncul miktarlari ve gecis kurallari

**Haftalik yuvarlama:** `wait_minutes>0` → oncul tetik haftasi Pazar 23:59:59 + bekleme; ardil alt sinir haftasi. Kesin gunluk cizelge degildir (`planning_granularity: weekly_approx`).

**Engel nedenleri:** `kapasite_yetersiz`, `oncul_eksik`, `bekleme_ufuk_disinda`, `rota_dongusu`

| Dosya | Degisiklik |
|---|---|
| `backend/app/services/operation_constraints.py` | **yeni** — bagimlilik, finish/cycles, bekleme |
| `backend/app/services/planning.py` | `_place_quantity` kisitli yerlestirme; WIP montaj |
| `backend/app/services/co_shipment.py` | ortak `_place_quantity` |
| `backend/app/services/job_moves.py` | `produced_map` ile oncul |
| `frontend/src/pages/Planning.tsx` | unplanned `reason` gosterimi |
| `backend/tests/test_transition_rules_planning.py` | B2/B3 regresyon (6 test) |

### FAZ 04 kabul olcutleri

| # | Olcut | Sonuc |
|---|---|---|
| 1 | Oncul kapasite 0 → ardil 0 | ✓ |
| 2 | finish+14 gun → ayni hafta baslamaz | ✓ |
| 3 | cycles lag 5 vs 50 tetik haftasi | ✓ |
| 4 | finish 20/100 → ardil baslamaz | ✓ |
| 5 | WIP montaj eksik kol bloklar | ✓ (assembly_outputs) |
| 6 | Rota dongusu hata | ✓ |
| 7 | Backend suite | **92 passed**, 1 flaky (`test_wip_multi_route`) (~74s) |
| 8 | Frontend build | Basarili |

## FAZ 05 — termin basarisizligi acik + zaman mantigi birlestirme

| Dosya | Degisiklik |
|---|---|
| `backend/app/services/planning.py` | `ScheduleOpResult`, ufuk tarihi, `_HOURS_LEFT_EPS`; `lead_time` partial/infeasible; tahmin guard |
| `backend/app/services/orders.py` | `plan_line_priority_key` (effective_due) |
| `backend/app/services/gantt.py` | effective_due siralama; `distribution_note` |
| `backend/app/schemas.py` | `LeadTimeOut`/`LeadTimeStep` genisletme; `GanttOut.distribution_note`; `ForecastFromLeadTimeIn.status` |
| `frontend/src/api.ts` | LeadTime + Gantt tipleri |
| `frontend/src/pages/Planning.tsx` | Terminleme hata/kismi UI; tahmin engeli |
| `frontend/src/pages/planning/GanttPanel.tsx` | Yaklasik dagilim notu |
| `backend/tests/test_leadtime_infeasible.py` | B4 + kabul testleri (6) |

### FAZ 05 kabul olcutleri

| # | Olcut | Sonuc |
|---|---|---|
| 1 | 0 kapasite 10 saat → end null, kalan 10, 2040 yok | ✓ |
| 2 | 15 dk is baslangic != bitis | ✓ |
| 3 | Ufuk disi bosluk basari sayilmaz | ✓ |
| 4 | Ileri plan dolulugu dusulur | ✓ |
| 5 | Basarisiz termin forecast kaydedilemez | ✓ |
| 6 | Gantt siralama effective_due | ✓ |
| 7 | Backend suite | **99 passed**, 1 flaky (`test_wip_multi_route`) (~28s) |
| 8 | Frontend build | Basarili (Vite 5.4.21) |

**Sonraki:** FAZ 06 — revizyon onay = hesaplanan plan (T1).
