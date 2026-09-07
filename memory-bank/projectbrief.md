# Proje Özeti — Sipariş İş Gücü İhtiyaçlarına Göre Üretim Kapasitesi Planlama

## Şirket / Bağlam
- **Şirket:** Bilge İnox — endüstriyel mutfak ekipmanları üreticisi.
- **Kapsam:** İş merkezi bazlı, sipariş kaynaklı **iş gücü (adam-saat) kapasite planlaması** ve günlük ilerleme takibi.
- **Neden MRP2 değil:** Üretim akışı ve reçete yapısı aşırı karmaşık; sahadaki makine/operasyonlardan anlık veri toplanamıyor. İş emri bazlı veri MRP2'ye günlük/anlık akmadığı ve akmayacağı için MRP2 sonlu kapasite planlama ve Gantt'ları anlamsız/takip edilemez olur. Bu program o boşluğu doldurur.

## Temel Hedef
Aktif siparişlerin (stok kodu + miktar + termin) BOM/çevrim süresi verilerinden **iş merkezi bazlı iş gücü ihtiyacını** hesaplamak, bunu tanımlı **verimli iş gücü kapasitesine** karşı planlamak ve günlük üretim/duruş verileriyle **plan ilerlemesini** izlemek.

## Zorunlu Gereksinimler (spec'ten)
1. **Web tabanlı, çok kullanıcılı** uygulama.
2. **Yetkilendirme matrisi:** admin / power user / user rolleri.
3. **Ücretsiz bir SQL veri tabanı** üzerinde çalışacak.
4. **Tüm ham veriler Excel ile import** edilecek; **tek tıkla Excel'e yedek (backup)** alınabilecek.
5. **Sipariş importu:** müşteri + termin bazlı aktif siparişler (stok kodu, miktar).
6. **BOM importu:** stok kodu başına hammadde + miktar, aşamalı tezgah/operasyon listesi ve her tezgaha tanımlı **cycle time**.
7. **İş merkezi seçimi:** pilot uygulama; tüm fabrika değil, seçilen iş merkezleri planlanır. Akış: *Seç → Planla → Kontrol et → Doğrula → Yeni iş merkezi planla.*
8. **Verimli çalışma süresi** tanımı iş merkezi bazında (kişi başı, gün başı). Vardiya düzenleri, çalışma günleri/saatleri, vardiyada kişi sayısı iş merkezi tablosunda tanımlanır.
9. **Personel listesi:** kimin hangi iş merkezinde çalıştığı → haftalık verimli iş gücü kapasitesi otomatik hesaplanır.
10. **Kapasite birimi:** kullanıcı tanımlı saat aralığı = "1 birim" (örn. 10 saat).
11. **Planlama modları:** **Otomatik** (terminlere göre) ve **Manuel** (kullanıcı seçer). İş gücü ihtiyacını toplayıp kapasiteyi dolduracak şekilde planlama.
12. **Çizelgeleme / sorgulama:** Çoklu stok kodu + seçilen tezgahlar + tarih aralığı için iş gücü ihtiyacı; tek stok kodunun toplam üretim süresi.
13. **Günlük ilerleme:** Bir önceki günün üretim sonuçları import edilir; planlanan vs. gerçekleşen iş gücü saati karşılaştırılır; kalan gün/saat gösterilir.
14. **Duruş analizi:** İş merkezi bazlı duruş verisi günlük import; olması gerekenden fazla duruşun sebep kırılımı **Excel raporu** olarak.
15. **Terminleme:** Yeni iş/iş grubu için seçilen operasyonların **saat bazında** başlangıç/bitiş tahmini.
16. **Çevrim süresi önerisi (öğrenme):** Üretim + duruş verilerinden dataset oluşturup, tanımlı cycle time'ın hatalı olabileceğini öngörmek ve ürün grubu / iş merkezi bazında yeni cycle time önermek (örn. 50 sn → 60 sn). Excel raporlama formatı.

## Kapsam Dışı (şimdilik)
- MRP2 entegrasyonu, anlık makine veri toplama, sonlu kapasite Gantt takibi.
- Tüm fabrikanın tek seferde planlanması (pilot iş merkezleriyle başlanır).

## Başarı Kriteri
Pilot iş merkezlerinde: siparişlerden hesaplanan iş gücü ihtiyacı, tanımlı kapasiteye göre planlanıp; günlük import edilen üretim/duruş verisiyle ilerleme ve sapma güvenilir şekilde raporlanabiliyor.
