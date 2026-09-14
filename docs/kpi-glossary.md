# KPI sözlüğü (M5 — FAZ 08)

| KPI | Formül / tanım | Birim | Veri kaynağı | Legacy davranış |
|-----|----------------|-------|--------------|-----------------|
| Planlanan saat | Haftalık plan satırları `planned_hours` toplamı | saat | `plan_lines` | Değişmedi |
| Standart saat karşılığı çıktı | İş merkezindeki `production_actuals.earned_hours` toplamı (hafta içi) | saat | `production_actuals` | UI’da eski ad `actual_hours` ile aynı değer |
| Plan eşleşen çıktı | Sipariş + operasyon eşleşen üretimin plan satırına yazılan saat payı | saat | `gantt._production_map` + plan satırları | Yoktu; plansız üretim plan kalanını düşürmez |
| Plan uyumu kalan | Σ max(plan_satırı − eşleşen_üretim, 0) | saat | `kpi_units.week_plan_and_output_kpis` | Eski `remaining_hours` = plan − tüm çıktı (hatalı) |
| Ölçülen adam-dakika (duruş) | `elapsed_minutes`: süre×kişi; `labor_minutes`: doğrudan | adam-dk | `downtimes` | `legacy_unspecified`: ölçüme dahil edilmez; `minutes` saklanır |
| Makine-dakika (duruş) | `machine_id` dolu ve legacy olmayan kayıtta `duration_minutes` | makine-dk | `downtimes` | Legacy sayılmaz |
| Oransal dağıtım (duruş) | Fazla adam-dk × (sebep ölçülen payı) | adam-dk | Analiz | Nedensel kayıp değil; etiket zorunlu |
| CT gözlemi | `reported_hours × 3600 / iyi_adet` | sn/adet | `production_actuals` | Earned saat payı dağıtımı kaldırıldı |
| CT önerisi | Medyan gözlem; eşik + min örnek | sn/adet | Analiz | Fiili süre yoksa öneri yok |
| Oransal plan ciro | Plan satırı × (plan saat / denom) | para | `revenue_report` | `earned_revenue` ile aynı |
| Planlanan sevk ciro | Tahmini bitiş haftasındaki sipariş cirosu | para | `order_schedule.planned_end` | Yeni alan |
| Gerçekleşen sevk ciro | Sevk miktarı × birim fiyat, sevk haftası | para | `shipments` | Yeni alan |

## Kalite alanları (üretim)

- `quantity`: uyumluluk; legacy ilerleme.
- `good_qty` / `scrap_qty` / `rework_qty`: doğrulanmış kalite.
- `quality_status`: `legacy_unspecified` → iyi adet sayılmaz (ilerleme `quantity` ile devam edebilir, `quality_unverified` uyarısı).

## Süre temelleri

- Üretim `reported_time_basis`: `labor_hours`, `elapsed_time`, `legacy_unspecified`.
- Duruş `time_basis`: `elapsed_minutes`, `labor_minutes`, `legacy_unspecified`.
