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
- Öncül/transfer: `leadtime_earliest_datetime` (bekleme dakika, haftaya yuvarlama yok)
- İş gücü havuzu: iş merkezi vardiya `headcount` toplamı; eşzamanlı `crew_size` toplamı aşılamaz

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
