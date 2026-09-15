# MES entegrasyonu — uygulanan kurallar ve doğrulama

Güncelleme: 15.09.2026. Kaynak örnek: `Uretim_Kayitlari_Raporu_14 Eylül 2026 17_38.xlsx`.
Gerçek dosya yalnızca önizlemede kullanıldı. Import denemesi canlı veritabanından alınan **ayrı bellek kopyasında** yapıldı; gerçek üretim, stok ve rezervasyonlara yazılmadı.

## Kullanıcının kesinleştirdiği veri sözleşmesi

- `Üretim Detay Id` tekildir. Bir başla/bitir kaydını temsil eder; silme/iptal yoktur. Dosyada bulunmayan kayıt silinmez.
- Miktar yalnızca `Net Üretilen Miktar`dır. Brüt, sağlam, hurda, rework ve iş emri toplamı kullanılmaz.
- Gün yalnızca `Tarih` sütunundan okunur. Dosya adı ve başlangıç/bitiş saatleri kullanılmaz.
- Müşteri, sipariş, pozisyon, lot ve MES bitmiş ürün ipucu üretim eşleştirmesine girmez.
- Bitmiş ürün adayları programın BOM/rota tanımlarından bulunur. Bitmiş ürün koduyla gelen üretim, kendi bitiş operasyonu ve BOM'u ile doğrulanır.
- İşçilik ilerlemesi net miktar × standart birim işçiliktir. MES süreleri alınmaz. Makine çevrimi bazlı rotalarda adet/çevrim ve tanımlı ekip büyüklüğü ile doğrusal birim işçilik hesaplanır; her adet ayrıca tam çevrime yuvarlanmaz.
- Ortak yarımamül önce bu haftanın bitmiş ürüne ulaşan planını destekler; kalan miktar yakın bitiş haftasından uzak haftaya dağıtılır. Aynı haftada plan satırı sırası kullanılır. Bitiş planı olmayan miktar ortak havuzda kalır.
- Bu dağıtım geçici planlama hesabıdır. Müşteri rezervasyonu yalnızca Stok & Rezervasyon ekranında yapılır.

## Kullanım

`/progress` → **MES Excel yükle** → önizlemeyi incele → **Onayla ve içe aktar**.

Önizleme yeni, güncellenecek, değişmeyen ve eşleşme bekleyen kayıtları gösterir. Her satırın net miktarı, makinesi, operasyonu ve tüketeceği yarımamüller görünür. Eşleşmeyen kayıtlar saklanır ama üretim/stok/işçilik hesabına katılmaz. Makine veya BOM/rota düzeltildikten sonra aynı dosyayı yeniden yüklemek bunları yeniden değerlendirir.

Kimlik bazlı güncelleme aynı gün içindeki farklı detayları birleştirmez. Aynı dosyanın tekrar yüklenmesi yeni üretim veya ikinci tüketim oluşturmaz. Önizlemeden sonra ilgili veri değişirse onay reddedilir; yeni önizleme gerekir. Tüm import tek işlemde kaydedilir. Rezerve/sevk edilmiş stoğu bozan miktar azaltmaları geri alınır.

## Malzeme akışı

Yarımamül üretimi ortak havuza giriş yapar. Tanımlı önceki aşama çıktısı, BOM katsayısıyla havuzdan düşer. Bitmiş ürün üretimi, ana dalın son yarımamülünü ve montaj bağlantısındaki yardımcı yarımamülleri tüketir; tüm ara aşamalar tekrar tekrar tüketilmez.

Bitmiş ürün için `source=mes` depo girişi kaynak detayına bağlı olarak bir kez oluşturulur/güncellenir. Eski genel üretim importunun otomatik depo girişine tekrar sokulmaz. MES depo girişi stok ekranından bağımsız silinemez.

Havuz, **MES başlangıcından itibaren net hareketleri** gösterir. Açılış stoğu eklenmiş kabul edilmez. Negatif bakiye fiziksel negatif stok iddiası değildir; eksik açılış/üretim verisini görünür kılar. Bu bakiyelerden pozitif olmayan miktar başka ürünlere dağıtılmaz.

Bitiş planı bölünmüşse yalnızca o haftanın miktarı ayrılır. Örnek: A ürününün bu hafta 10, iki hafta sonra 90; B ürününün gelecek hafta 40 adetlik bitiş planı ve 60 adet ortak yarımamül varsa dağıtım **A:10 → B:40 → A:10** olur. Toplam tahsis 60 adedi geçmez. Üretim partileri için yalnızca dayanak sipariş miktarı değil, parti talebi dikkate alınır.

## İlerleme ekranı

- Rapor tarihi kendi pazartesi–pazar haftasını seçer; seçilen gün dahildir.
- Haftalık plan, üretimin standart işçilik karşılığı, kalan standart işçilik, kapasite kullanımı ve plan dışı işçilik kartları.
- Günlük ve birikimli üretim grafikleri. Haftalık plan çizgisi toplam hedeftir; sahte bir günlük plan oluşturulmaz.
- İş merkezi karşılaştırması; operasyon bazında plan/bu hafta/önceden/kalan adet ve kalan işçilik. Günlük adetler ve planın bitmiş ürün adayları açılabilir.
- Haftalık operasyon hedefi bir kez doldurulur. Fazla üretim yakın ufukta aynı malzeme/iş merkezi/operasyona eşlenir; erken üretim haftanın plan başarısını yapay olarak artırmaz.
- Ortak havuzun güncel bitiş planlarına dağılımı ayrıca gösterilir. Üretim kaydı veya müşteri rezervasyonu değişmez.
- İş merkezi çoklu filtresi, stok/ürün/operasyon araması ve Excel aktarımı. Excel; günlük sonuçlar, operasyonlar, plan dışı kayıtlar, dağıtım ve veri kontrollerini içerir.

**Oranlar:** Üretim/plan = tüm standart üretim saati / haftalık plan saati. Kapasite kullanımı = standart üretim saati / rapor tarihine kadar kullanılabilir işçilik. Bunlar fiili çalışılan süre ölçümü değildir. Sıfır paydada yüzde gösterilmez. Kalan işçilik hazırlık süresi hariç kalan adet × standart birim saattir.

## Plan referansı

İlk MES importunda mevcut plan haftaları ve sonraki 12 haftanın tanımlı hedefleri korunur. Yeniden planlama bu karşılaştırma hedefini değiştirmez. Henüz plan bulunmayan haftalarda güncel plan gösterilir; arayüz referans durumunu açıkça belirtir. Sağlıklı başlangıç karşılaştırması için haftalık plan MES yüklemesinden önce yayımlanmalıdır. Plan öncesi tarihsel üretimden geriye dönük özgün plan türetilemez.

MES importu planı kendiliğinden yeniden çizelgelemez. Mevcut bitiş planına ayrılan kullanılabilir yarımamüller kalan iş hesabına girer; kullanıcı planlamayı çalıştırınca yeniden çizelgelenir. Raporun üretim gerçekleşmeleri müşteri bazlı kalan iş tahsisinden bağımsızdır.

## Gerçek örnek dosyanın kontrol sonucu

- 139 satır, 139 farklı detay kimliği; net toplam 10.362 adet.
- Tarih dağılımı: 14.09.2026: 133; 13.09.2026: 5; 04.08.2026: 1.
- XLSX stil hatası, stilleri hiç okumayan değer adaptörüyle aşıldı; kaynak dosya değiştirilmedi.
- Mevcut ana veride 115 kayıt eşleşti, 24 kayıt inceleme gerektiriyor. Bunlar 14 farklı malzeme koduna ait.
- Örnek tutarsızlık: `5800011-02` için bir rota `5800011-01` sonrasına, diğeri `5800011-19` sonrasına geçiyor. Tüketilecek aşama farklı olduğundan otomatik seçim yapılmıyor.
- `6005638` satırının makine kodu tanımlı değil; bitmiş ürün girişi tahmin edilerek açılmıyor.
- Önizleme, mevcut MES açılış verisi bulunmadığı için 27 yarımamülde eksik bakiye gösterdi. Bu kayıtlar ilk entegrasyonun başlangıç stoğuyla uzlaştırılmalı.
- Canlı verinin ayrı bellek kopyasında ilk rapor 35,6 saniyeydi; gereksiz iş ve sorgular kaldırıldıktan sonra 0,2–1,3 saniye aralığına indi. Bu ölçüm plan satırı olmayan mevcut örnek durum içindir; büyük aktif planlar ayrıca yük oluşturur.

## Teknik yüzey ve kabul kontrolleri

`POST /api/mes/preview`, `POST /api/mes/import?token=…`, `GET /api/mes/progress`, `GET /api/mes/progress.xlsx`.
Kaynak tabloları: `mes_details`, `mes_plan_baselines`. Genel üretim importu kendi eski akışını korur; aynı üretim iki import türüne birden yüklenmemelidir.

İzole testler: bozuk stiller, yalnızca net/tarih kullanımı, geçersiz miktarlar, tekrarlı kimlik, idempotent import, detay düzeltmesi, tek depo girişi/tüketim, eskimiş önizleme, belirsiz standartlar, haftalık hedef ve erken üretim, korunan plan, bitiş haftasına göre ortak havuz, bölünmüş plan, partisiz/partili talepler, tüketilen yarımamülün tekrar kullanılmaması, kapasite oranları, pazar günü, rezervasyonları koruyan geri alma ve Excel metin güvenliği.
