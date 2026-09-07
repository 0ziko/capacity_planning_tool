# Sistem Desenleri

## Genel Mimari
```
[Excel .xlsx] --import--> [FastAPI /api] <--SQLAlchemy--> [PostgreSQL | SQLite]
                              ^   |
        [React SPA (Vite)] ---+   +--export--> [Excel: yedek, plan, duruş, çevrim süresi raporları]
```
- Tek backend, tek SPA. Geliştirmede Vite `/api` isteklerini :8000'e proxy'ler; CORS da açık.
- Hesaplama motoru `app/services/*` içinde, API'den bağımsız; testler doğrudan API üzerinden akışı doğrular.

## Dizin Yapısı
```
backend/app/
  core/    config.py (pydantic-settings), security.py (bcrypt+JWT), deps.py (get_current_user, require_role)
  db/      session.py (Base, engine, SessionLocal, get_db)
  models/  user.py · master.py (WorkCenter, WorkCenterShift, WorkCenterWeek, Machine, Employee, Item, BomLine, RoutingOperation, OpTransitionRule, norm_op)
           planning.py (Order, PlanLine, ProductionActual, Downtime, ImportLog, StockReceipt, Reservation, Shipment)
  schemas.py   (tüm Pydantic şemaları)
  services/ capacity.py (Overrides) · requirements.py · planning.py · progress.py · analysis.py · excel.py · scenarios.py (RuleLookup, flow) · stock.py (rezervasyon/sevk)
  api/     auth.py (login, users) · master.py (iş merkezi, vardiya, personel, stok, sipariş)
           planning.py (kapasite, ihtiyaç, plan, yük, terminleme, ilerleme, analiz, raporlar) · imports.py (şablon, upload, log, yedek)
           scenarios.py (/api/scenarios: groups, flow, rules) · stock.py (/api/stock: summary, orders, receipts, reservations, shipments)
  main.py  (lifespan: create_all + admin seed; router kaydı)
frontend/src/
  api.ts (fetch sarmalayıcı, tipler, tarih yardımcıları) · auth.tsx (context, roller) · components.tsx (ortak parçalar)
  App.tsx (yan menü + rotalar) · pages/*.tsx (12 sayfa: + Scenarios, Stock) · pages/WcWeeksPanel.tsx · pages/planning/*
```

## Veri Modeli (özet)
- **WorkCenter**: code, name, is_active, **is_planned** (pilot), capacity_unit_hours ("1 birim"), default_efficient_hours, **area_code / area_name** (alan = İM grubu, örn. PRS / PRESHANELER), **capacity_source** (`work_center` | `machines`)
- **WorkCenterShift**: weekdays "0,1,2,3,4", start/end, headcount (0 ⇒ personelden), efficient_hours_per_person (null ⇒ iş merkezi varsayılanı)
- **Machine**: work_center_id (CASCADE), code (benzersiz), name, description, is_active — İM altındaki makine/tezgah; isteğe bağlı detay
- **Employee**: code, name, work_center_id, **machine_id** (SET NULL; makine personelin İM'sine ait olmalı — API/import doğrular)
- **Kapasite kişi sayısı kaynağı** (`capacity.employee_count(db, wc)` + `shift_headcount(shift, emp, wc)`): `work_center` ⇒ vardiya headcount>0 ise o, yoksa İM'ye bağlı aktif personel; `machines` ⇒ İM'nin aktif makinelerine atanmış aktif personel, vardiya headcount yok sayılır. Kullanıcı İM satırından anlık değiştirir ("makine detayını istediğimde aktifleştir")
- **WorkCenterWeek**: (work_center_id, week_start) benzersiz; headcount / efficient_hours_per_person / working_days **nullable** — null ⇒ vardiya/İM varsayılanı. `capacity.Overrides(db, wc_id)` haftalık sözlük; tüm kapasite fonksiyonları `ov` parametresiyle çalışır. Etkin kapasite = kişi × verimli saat × gün.
- **OpTransitionRule**: scope `group|item`, product_group, item_id, from_op/to_op (+ `_norm`: Türkçe karakter/boşluk normalize), rule `finish|cycles`, lag_cycles, wait_minutes. Öncelik: stok > grup > varsayılan(finish). `cycles` ⇒ sonraki op öncekinin `lag/qty` oranında başlar (iç içe), öncekinden önce bitemez.
- **Item** → **BomLine**[] (hammadde) + **RoutingOperation**[] (seq, work_center, cycle_time_sec, setup_time_min)
- **Order**: order_no, customer, due_date, item, quantity, status(open/closed)
- **PlanLine**: order, operation, work_center, week_start (Pazartesi), planned_hours, planned_qty, mode(auto/manual)
- **ProductionActual**: prod_date, wc, item, operation_seq, order_no, quantity, earned_hours (CT'den), reported_hours (opsiyonel fiili)
- **Downtime**: dt_date, wc, reason_code/desc, minutes
- **StockReceipt** (item, receipt_date, quantity, lot, source manual|excel), **Reservation** (item, order CASCADE, quantity, source manual|auto), **Shipment** (item, order, ship_date, quantity). Serbest = Σgiriş − Σrezerve − Σsevk (ürün bazında). Sipariş kalan = miktar − rezerve − sevk; tamamen sevk ⇒ `Order.status=closed`.
- **ImportLog**, **User**

## Temel Hesaplar (services)
- **Günlük verimli kapasite** = Σ_vardiya (kişi × kişi başı verimli saat) — `capacity.daily_capacity_hours`
- **Haftalık kapasite** = Σ günler; **birim** = saat / capacity_unit_hours
- **Operasyon saati** = miktar × cycle_time_sec / 3600 + setup_min / 60 — `RoutingOperation.hours_for`
- **İhtiyaç** = açık siparişler (veya verilen miktarlar) × rota — `requirements.requirement_lines`
- **Otomatik plan**: siparişler termine göre; her operasyon için haftalara sırayla doldur (kalan = kapasite − manuel); sonraki op ≥ önceki op'un ilk haftası; sığmayanlar `unplanned` — `planning.auto_plan`
- **İlerleme**: beklenen = planlanan × geçen çalışma günü / toplam gün; gerçekleşen = Σ earned_hours (as_of'tan önceki günler); kalan gün = kalan saat / günlük kapasite — `progress.week_progress`
- **Duruş**: beklenen dk = (nominal − verimli) × kişi × 60; fazla = gerçekleşen − beklenen; sebep payı ile fazla duruş dağıtımı — `analysis.downtime_analysis`
- **Çevrim süresi önerisi**: etkin CT = reported_hours×3600/qty, yoksa (günlük verimli saat − fazla duruş) × (kazanılan saat payı) × 3600 / qty; medyan; min örnek + sapma eşiği — `analysis.cycle_time_suggestions`
- **Terminleme**: gün gün boş kapasite (kapasite − haftalık plan/gün sayısı) tüketilir; verimli saat nominal mesai saatine oransal yayılır → saat:dakika — `planning.lead_time`

## Excel Import Deseni (`services/excel.py`)
- `TEMPLATES[kind]` = sütunlar (key, Türkçe başlık, alias'lar), örnek satır, zorunlu alanlar.
- Başlık eşleme `norm()` ile (Türkçe karakter + boşluk/noktalama bağımsız).
- Her tür için `import_<kind>(db, rows)` upsert; hatalar satır numarasıyla toplanır; `ImportLog` yazılır.
- Yedek: her tablo bir sayfa, başlıklar şablonla aynı → yedekten geri yükleme mümkün.

## Yetki
- `require_user` (görüntüleme/rapor), `require_poweruser` (import, tanım, plan), `require_admin` (kullanıcılar). Frontend `can(role)` ile butonları gizler; asıl kontrol backend'de.

## Tasarım Kararları
- Plan granülerliği **hafta**; ilerleme granülerliği **gün**.
- Setup süresi planlamada dahil, günlük üretimden "kazanılan saat"te hariç.
- Pilot: planlanmayan iş merkezleri görünür ama otomatik plana girmez.


## Sipariş Birleştirme (v0.2)
- Aynı `item_id` (dolayısıyla aynı rota) olan açık siparişler öneri grubudur. Birleştirme yeni bir `Order` (status=open) yaratır; kaynaklar `status=merged` ve `merged_into_id` ile bağlanır, plan satırları silinir. Geri alma: kaynaklar yeniden `open`, birleşik sipariş silinir (plan satırları cascade).
- Import (`import_orders`) merged siparişleri eşleşme anahtarına almaz; böylece aynı sipariş no tekrar yüklenince yeni açık kayıt oluşur.

## Sipariş Bitiş Tarihi Tahmini
- `order_schedule`: siparişin plan satırları → ilk hafta, son hafta; son haftadaki İM'de satırlar termin sırasıyla kümülatif doldurulur, siparişin payı bittiği noktadaki çalışma günü bitiş günüdür (`ceil(küm/kapasite × gün sayısı)`).

## Planlama Sayfası Yapısı
- `pages/Planning.tsx` üst filtre paneli (hafta, İM, planlama modu, otomatik plan) + sekmeler; sekme içerikleri `pages/planning/*Panel.tsx` dosyalarında (OrderSchedule, WcOrders, Revenue, Compare, OrderProgress, Merge).

## Planlama Motoru: Simülasyon + Modlar (v0.3)
- `planning.simulate(db, req) -> Simulation` kalıcı değildir (`DraftLine` dataclass, PlanLine ile aynı alan adları + `order` referansı); `auto_plan` simülasyonu `PlanLine` olarak yazar (`mode=auto`, `strategy=req.mode`). Manuel satırlar kapasiteden önce düşülür.
- `_place_order` tek siparişi kalan kapasiteye yerleştirir (operasyon sırası kısıtı korunur) ve `remaining` sözlüğünü günceller.
- `due_date`: termin sırası, kısmi yerleşim serbest. `revenue`: ciro/saat azalan sıra; her sipariş kapasite kopyası üzerinde denenir, tamamen sığmazsa atlanır; sonra atlananlar termin sırasıyla kısmen yerleştirilir (`skipped` = hiç yerleşemeyenler).
- `orders.order_schedule(db, wc_ids, lines=None, orders=None)` hem DB hem simülasyon satırlarıyla çalışır → karşılaştırma aynı bitiş tarihi mantığını kullanır.

## Veri Bütünlüğü
- SQLite'ta `PRAGMA foreign_keys=ON` (session.py connect event). İş merkezi silme, bağlı rota/plan/üretim/duruş varsa 400 ile engellenir; çalışanların İM'si NULL'a çekilir; vardiyalar cascade.
- `db/migrate.repair_orphans` açılışta ana kaydı silinmiş satırları temizler (`_ORPHAN_CHECKS` listesi). Servisler yine de `op.work_center is None` durumuna toleranslıdır.

## Ciro (v0.3)
- `Order.unit_price`; ciro = quantity × unit_price (tek para birimi). Birleştirilen siparişte ağırlıklı ortalama fiyat.
- `revenue.revenue_report`: completed (bitiş gününün haftası/ayı, tüm ciro) + earned (satır saat payı × ciro; tamamen planlananda pay planlanan toplam saate göre). Dönem listesi ufuk + ufuk dışına taşan bitişleri kapsar.
- `revenue.compare`: iki simülasyon → `PlanScenario` (özet KPI + çizelge + ciro) + `CompareOrderRow.diff` sınıflandırması.

## Haftalık İş Gücü (v0.5)
- Vardiya tanımı **varsayılan**; `WorkCenterWeek` sadece **farkı** saklar (null alan = varsayılan). UI'da istisna turuncu; "Varsayılan" düğmesi kaydı siler.
- Tüketiciler `cap.Overrides` üzerinden okur: planlama (`_daily_free_hours`, `_schedule_op`), analiz (beklenen üretim), ilerleme (çalışma günü), sipariş bitiş tahmini. Yeni bir kapasite hesabı yazarken `ov = ovl.get(day)` alıp `cap.*(…, ov)` çağır.
- Excel `wc_weeks`: hafta kolonu tarih (herhangi bir gün → Pazartesi) ya da `YYYY-Www`.

## Senaryo Matrisi (v0.5)
- Akış = ürün grubundaki rotaların operasyon adına göre birleşimi (`_merge_sequences`); stok seçilirse o stokun rotası. Düğüm: operasyon + İM + çevrim; ok: etkin kural + kaynağı (varsayılan/grup/stok).
- Kural uygulaması iki yerde: `planning._place_order` (otomatik plan; önceki op'un ilk/son hafta indeksi → `cycles` ise öncekinin ilk haftasından itibaren, `finish` ise son haftasından itibaren yerleşir) ve `planning.lead_time` (saat bazlı; `_schedule_op` boş kapasiteye yayar).
- Operasyon adı eşleşmesi `norm_op` ile (Sıvama = SIVAMA = sivama).

## Stok & Rezervasyon (v0.5)
- Akış: **Depo girişi** (manuel/Excel) → **Serbest stok** → **Manuel rezerve** (kritik) → **⚡ Otomatik** (termin sırası, kalan stoğu dağıtır) → **Sevk** (stoktan düşer; sipariş kapanır) → **Geri al**.
- Manuel rezervasyon serbest stok yetmezse otomatik rezervasyonları en geç terminliden başlayarak çözer; manuel kayıtlara dokunmaz. Otomatik dağıtım manuel kayıtları sabit kabul eder.
- Üretim ilerlemesi ile bağ yok (bilinçli): `ProductionActual` rezervasyonu etkilemez; depo girişi ayrı kayıttır.
