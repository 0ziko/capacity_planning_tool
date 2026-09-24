# Pilot günlük çizelge (FAZ 11)

## Kapsam

- Kullanıcı **Planlama → “Günlük ayrıntılı çizelge (pilot)”** ile `planning_granularity=daily_detailed` seçer.
- Haftalık `PlanLine` üretimi aynı kalır (karar destek).
- Ek olarak `plan_operation_segments` tablosuna makine zaman aralıkları yazılır.

## Yerleştirme koşulları

- `time_basis != legacy_unspecified` ve `missing_resource_definition == false`
- Uygun makine yalnızca birincil/alternatif istasyon listesinden; en erken bitiş, eşitlikte makine kodu
- Malzeme `strict`: unknown planlanmaz; `ready` için `material_ready_date` öncesi yok
- Takvim: FAZ 10 makine aralıkları + tatil/bakım; işlem yalnız bu aralıklarda
- Öncül/transfer: gerçek çevrim tamamlanma olayları + ortak `max_successor_qty` kuralı; bekleme dakika olarak uygulanır, haftaya yuvarlanmaz.
- Yarımamul–mamul: mevcut `explode_order` BOM açılımındaki dallar kendi rota sıraları ve kendi stok koduna özel senaryolarıyla çizelgelenir. İlk mamul operasyonu, bütün yarımamul dallarının tamamlanan miktarlarına `assembly_cap_qty` BOM katsayısı uygulanarak beslenir. Eksik bağlı rota mamulü serbest bırakmaz.
- İş gücü havuzu: haftalık kişi sayısı tüm vardiyaların ortak toplamıdır; vardiya sayısıyla çarpılmaz. Eşzamanlı ekip toplamı ve günlük kişi-saat bütçesi aşılmaz. Boş kişi sayısı açık ön kontrol onayı olmadan plan uygulamayı engeller; onay verilirse kapasite sıfırdır, kayıt boş kalır.

## Setup / parti

- Ayrı `setup` segmenti; aynı parti + aynı makine kesintisiz devam → tek setup
- Başka parti araya girerse veya makine değişirse yeni setup
- `SetupFamilyTransition` (from_family, to_family, machine_id, setup_minutes) varsa kullanılır
- Tanımsız setup 0 sayılmaz (`setup_unknown` → günlük plan dışı)

## API

- `GET /api/plan/segments?work_center_id=&start=&end=`
- `PATCH /api/plan/segments/{id}/lock?locked=true`
- Gantt `segments[]` alanı segmentlerle aynı `start_at`/`end_at`

## Sınırlar

- Global optimum yok; Faz 03 aday sırası + yerel makine seçimi
- Kapasite yetmezse `remaining_qty` raporu; uydurma termin yok
- Kilitli segmentler otomatik planda korunur


## D1 kapanış doğrulaması — 17.09.2026

- İki yarımamul dalı, 2:1 BOM katsayısı, yarımamul ve mamulün çok operasyonlu rotaları test edildi.
- Kısmi yarımamul çıktısı, eksik dal/rota, stok koduna özel geçiş, seçim dışı öncülün tamamlanmış varsayılmaması ve mevcut gerçekleşme kredisinin tüketilmiş mamul miktarıyla birlikte ele alınması test edildi.
- Haftalık otomatik plan → günlük segment → Gantt zinciri izole SQLite üzerinde doğrulandı.
- Günlük plan API sonucu operasyon kimliği/stok kodunu taşır; Planlama ekranı eksik kaynak ve kalan miktarları gösterir. Bu rapor yalnızca günlük çizelgeye aktarılan işleri kapsar; haftalık yerleşmeyen işler ayrı rapordadır.
- Eksik gerçek makine/operasyon master tanımları veri hazırlığıdır. Bunlar tamamlanmadan sahada günlük pilot hazır ilan edilmez; canlı çizelge üretilerek veri değiştirilmedi.
