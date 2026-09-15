# Üretim ilerlemesi: teslimat etkisi

`GET /api/mes/delivery-risk` salt okunur bir senaryo hesaplar. Üretim, rezervasyon,
parti üyeliği veya plan kaydı yazmaz. Müşteri / sipariş / poz yalnızca çıktıdaki etki izidir.

## Hesap

- Rapor tarihine kadar sevkleri talep ve stoktan düşürür. Mevcut rezervasyonları korur;
  serbest bitmiş stoğu etkin termin sırasıyla yalnızca bir kez paylaştırır.
- Kalan talebi BOM yarımamül katsayıları ve operasyon rotalarına açar.
- Net MES yarımamül havuzunu güncel bitiş planının haftasına göre, yakın haftadan
  uzağa tüketir. Parti planındaki miktarlar bellekte üye pozlara bölünür; aynı fiziksel
  miktar iki ürüne yazılmaz. Bitiş planı olmayan miktar havuzda kalır.
- İşçilik net kalan adet × standart süre ve kalan operasyon başına bir hazırlıktır.
  MES saatleri kullanılmaz. İş merkezi haftalık istisnaları, takvimi ve planlama
  rezervi uygulanır. Rapor günü tamamlanmış sayılır; kapasite ertesi gün başlar.
- Geriye doğru hesap her siparişin kendi zincirini tam kapasite üzerinde termine
  yerleştirir. Bu tarihler gerekli üst sınırlardır, tüm siparişlere kapasite garantisi değildir.
- İleriye doğru hesap tüm ilgili açık siparişleri aynı günlük kapasite havuzunda
  termin önceliğiyle yerleştirir. Ardışık operasyonlar çakışmaz; iş merkezinin aynı
  günündeki boş zaman dilimleri ortak kullanılır. Tanımlı malzeme hazır tarihi uygulanır.
- Ekran filtreleri hesap bittikten sonra uygulanır; gizlenen işlerin yükü korunur.

## Sınırlar

Tam parti bitişi esas alınır; kısmi sevkiyat tarihi taahhüt edilmez. Çevrimle örtüşen
geçişler ihtiyatlı tam parti hesabı ve belirsizlik uyarısı ile gösterilir. Makine bazında
çizelgeleme veya paralellik / parti birleştirme optimizasyonu yapılmaz. Rota sırası ve
bağımsız yarımamül dallarının bitiş rotasına beslenmesi kullanılır. Malzeme hazır tarihi
yoksa malzeme hazır varsayılır; hammadde satın alma hesabı yapılmaz.

Eksik süre sıfır işçilikle kesin teslim tarihi oluşturmaz. Ortak kapasitede bu eksikten
etkilenen sonraki işler de belirsiz işaretlenir. Eksik rotalar ve sıfır kapasite ayrıntıda
gösterilir. Bilinmeyen işçilik haftalık yük toplamında sayısallaştırılamadığından toplamlar
yalnızca tanımlı işçiliği içerir. Eski açık terminler yük hesabına dahildir.

İleri takvim seçilen ufuktan sonra 26 hafta, geriye takvim rapor tarihinden önce 26
haftadır. Bu sınırların dışındaki sonuçlar kesin tarih olarak yorumlanmamalıdır.
Stok / rezervasyon mevcut durumdur; geçmiş güne dönük tam muhasebe yeniden kurma değildir.

## Doğrulama

İzole test veritabanında kapasite çakışması, geriye tarih hesabı, stok / rezervasyon /
sevk tekilleştirme, eksik süre, ortak yarımamül önceliği, parti üyeleri, malzeme hazır
tarihi ve analiz boyunca INSERT / UPDATE / DELETE olmaması test edilir.
