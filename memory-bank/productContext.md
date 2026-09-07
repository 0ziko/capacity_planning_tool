# Ürün Bağlamı

## Neden Var?
Bilge İnox'ta üretim planlama fiilen Excel + manuel hesaplarla yürüyor. MRP2'nin sonlu kapasite planlaması, sahadan iş emri bazında veri akmadığı için kullanılamıyor. Planlamacının ihtiyacı: **siparişlerden iş gücü ihtiyacını hesaplayan, gerçek (verimli) kapasiteyle karşılaştıran ve günlük import edilen verilerle ilerlemeyi gösteren** hafif bir araç.

## Çözdüğü Problemler
| Problem | Çözüm |
|---|---|
| Sipariş yükünün iş merkezlerine adam-saat olarak dağılımı bilinmiyor | BOM + cycle time x sipariş miktarı -> iş merkezi bazlı saat ihtiyacı |
| Nominal mesai != gerçek üretken süre (malzeme bekleme, mola, iç lojistik, setup) | İş merkezi bazında **verimli çalışma süresi** (kişi/gün) tanımı; kapasite = kişi x verimli saat x gün |
| Plan yapıldıktan sonra ilerleme görülmüyor | Günlük üretim importu -> planlanan vs gerçekleşen, kalan saat/gün |
| Duruşların etkisi ölçülmüyor | Günlük duruş importu -> sebep kırılımlı fazla duruş raporu (Excel) |
| Cycle time'lar tahmini ve hatalı olabilir | Biriken gerçek veriden cycle time önerisi (ürün grubu / iş merkezi bazlı) |
| Yeni iş için termin verilemiyor | Mevcut kapasite kullanımına göre operasyon bazlı saatlik başlangıç/bitiş tahmini |

## Kullanıcılar ve Roller
- **Admin:** kullanıcı/rol yönetimi, tüm master data, yedekleme.
- **Power user (planlamacı):** import, iş merkezi/vardiya/verimli süre tanımları, planlama (otomatik/manuel), raporlar.
- **User:** görüntüleme, belirli raporlar (yetki matrisiyle daraltılır).

## Nasıl Çalışmalı (akış)
1. Master data import: iş merkezleri, personel listesi, BOM (hammadde + operasyon rotası + cycle time).
2. İş merkezi tablosunda: çalışma günleri, vardiya saatleri, vardiyadaki kişi sayısı, kişi başı verimli süre, kapasite birimi (örn. 10 saat = 1 birim).
3. Sipariş import (müşteri, termin, stok kodu, miktar).
4. Planlanacak iş merkezlerini seç (pilot).
5. Otomatik (termin sıralı) veya manuel planlama -> haftalık kapasite doluluğu (saat ve birim).
6. Her gün: önceki günün üretim ve duruş verisini import et -> ilerleme, sapma, kalan iş.
7. Raporlar: Excel'e yedek, duruş raporu, cycle time öneri raporu.

## Örnek Hesap (spec'ten)
- İş merkezi: 10 kişi, kişi başı **4 saat verimli** süre/gün, 08:00-18:00, haftada 5 gün.
- Kapasite = 10 x 4 x 5 = **200 saat/hafta**.
- Kapasite birimi 10 saat -> **20 birim/hafta**.
- Bu haftaya 200 saat iş yüklenmişse dengeli ilerleme günlük **40 saat** olmalı; import sonrası gerçekleşen bununla karşılaştırılır.

## Kullanıcı Deneyimi Hedefleri
- Excel-merkezli çalışma alışkanlığına uyum: her veri Excel'den gelir, her çıktı Excel'e gider.
- Az tıkla planlama; tablo odaklı ekranlar; iş merkezi filtreleme her yerde.
- Türkçe arayüz.
