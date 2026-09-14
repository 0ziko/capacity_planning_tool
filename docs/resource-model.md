# Kaynak modeli (FAZ 10 — pilot)

## Amaç

İş gücü (adam-saat) ile makine-saat ihtiyacını ayırmak; günlük takvim istisnalarını haftalık planın üzerine koymak. **Canlı rotalarda otomatik süre türü dönüşümü yapılmaz.**

## RoutingOperation alanları

| Alan | Açıklama |
|------|----------|
| `time_basis` | `legacy_unspecified` (varsayılan), `labor_seconds_per_unit`, `machine_seconds_per_cycle` |
| `cycle_time_sec` / `setup_time_min` | Legacy ve import uyumu; mevcut veri olduğu gibi kalır |
| `crew_size` | Eşzamanlı ekip; geçen süre = iş gücü içeriği / ekip |
| `machine_cycle_time_sec` | Makine ihtiyacı ayrı verilmedikçe labor modunda çıkarılmaz |
| `units_per_cycle` | Makine modunda zorunlu pozitif (varsayılan 1); günlük yerleştirmede çevrim sayısı yukarı yuvarlanır |
| `setup_labor_minutes` / `setup_machine_minutes` | Ayrı setup; boşsa legacy `setup_time_min` davranışı basis’e göre |

## Birim dönüşümü

Tek servis: `backend/app/services/routing_resource.py`

- Haftalık plan yükü: `legacy_unspecified` → eski `quantity×cycle/3600 + setup/60`
- Ayrıntılı çizelge: `missing_resource_definition` uyarısı (legacy ve makine listesi eksik operasyonlar)

## Takvim

- `machine_calendar_entries`: makine günü çalışma/bakım aralığı
- `resource_calendar_exceptions`: iş merkezi tatili veya makine bakımı; **gün bazında haftalık `WorkCenterWeek` override’ından öncelikli**
- Makine kapasitesi: `calendar_capacity.machine_daily_capacity_hours` — **personel sayısından türetilmez**
- Gece vardiyası: `expand_shift_to_ranges` iki gerçek tarih aralığı üretir

## UI / Excel

- Stok/Rota ekranında power user operasyon kaynak alanlarını düzenleyebilir
- Excel Rota sayfasına yeni kolonlar eklendi; eski şablonlar (kolonsuz) import edilebilir
- `GET /api/resource-model/stats` — legacy ve eksik tanım sayıları
