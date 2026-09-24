# Değişiklik öncesi çalışma notları — 17.09.2026

Bu belge uygulama hazırlığı için mevcut davranışları, bağımlılıkları ve doğrulama ihtiyaçlarını kaydeder. Onaylanmış uygulama planı veya tüm ekranların uçtan uca kabul belgesi değildir. Kaynak kod esas alınmıştır; mevcut yerel değişiklikler korunmuştur.

## Korunacak iş kuralları

- Üretim müşteri siparişinden ayrılabilir; aynı ürüne ait ihtiyaçlar üretim partisinde birleştirilebilir.
- Toplu üretim yapılırken müşteri/sipariş/pozisyon için planlanan pay görünür kalmalıdır.
- Tamamlanan ürünün müşteriye fiilî tahsisi mevcut Stok & Rezervasyon akışından yapılır. Otomatik dağıtım ve manuel müşteri seçimi zaten vardır; ikinci bir tahsis motoru kurulmaz.
- Kullanıcı gerçekleşme kaynakları ayrımını onayladı: ne kadar üretildiği MES'ten, hangi müşterinin karşılandığı rezervasyon/sevkiyattan okunacak. Eski üretim raporunun FIFO dağılımı müşteri rezervasyonu kabul edilmeyecek. Geçişte eski kayıtlar silinmeyecek.
- İlk planlama amacı, güncel rezervasyon ve sevkiyat farklı bilgilerdir. Bir müşteriden diğerine stok aktarılması ilk planlama amacını geriye dönük değiştirmemelidir.
- Sipariş haftalara bölünebilir. Termin içinde yerleşen, geç yerleşen ve ufuk dışında kalan ihtiyaç ayrı gösterilmelidir.
- İş gücü tanımlanmamış hafta ile açıkça sıfır kapasite girilmiş hafta ayrılmalıdır.
- Tahminî bitmiş ürün hazır tarihi son operasyon bitişidir. Bu tarih fiilî stok girişi oluşturmaz.
- Belirsiz saha verisi uydurulmaz. Canlı veride reset/seed yapılmaz; commit/push ayrıca istenir.

## Modül ve bağımlılık envanteri

### Özet
Kapasite, yük ve üretim göstergeleri. Planlama kapasitesindeki değişiklikler buraya da yansır. Eski üretim kayıtlarına dayanan göstergeler ile MES göstergelerinin kapsamı aynı varsayılmamalıdır.

### Stok kartları, reçete ve rota
Ürün grupları, BOM ağacı, yarımamul bağlantıları, operasyon süreleri, ekip/setup/makine bilgileri ve alternatif kaynaklar. `bom_tree.py`, `production_bom.py` ve `wip.py` planlamanın ortak girdileridir. Bazı raporlar genişletilmiş yarımamul rotası yerine ürünün doğrudan operasyonlarını okur; rota değişikliği yalnızca ana planlayıcıda ele alınamaz.

### İş merkezleri ve personel
İş merkezleri, makineler/istasyonlar, vardiyalar, takvim ve haftalık iş gücü girdileri birlikte kapasite üretir. Personel ekranını kaldırmak; kapasite varsayımları, import şablonları, yedekleme ve yönetim ekranlarını da etkiler. Haftalık iş gücü mevcut sistemde vardır; yeniden yazılacak bir modül değildir.

### Siparişler
Sipariş/pozisyon, müşteri, ürün, miktar, termin/revize termin, fiyat ve malzeme durumu. Düzenleme, Excel karşılaştırmalı import ve eksik kayıtları kaldırma seçenekleri; plan, üretim partisi, rezervasyon ve sevkiyat bağımlılıkları taşır.

### Senaryolar
Ürün, grup ve varsayılan kapsamındaki operasyon geçiş kuralları; bitiş bekleme, çevrim ve bekleme süresi. Haftalık/günlük çizelgeleme, termin tahmini ve Gantt aynı kural anlamını korumalıdır.

### Planlama: 12 alt görünüm
- Haftalık yük: kapasite, plan satırları, manuel ekleme/düzenleme ve tahmin yükü.
- Haftalık üretim: operasyon bazlı planlanan çıktı; farklı operasyon adetleri toplanıp bitmiş ürün sayısı gibi yorumlanmamalıdır.
- Gantt: operasyon zamanlaması ve iş merkezi görünümü.
- İş gücü: haftalık kişi/saat/gün girdileri.
- Revizyon: gerekçe, simülasyon, eski/yeni plan, onay ve veri değişikliği kontrolü.
- Sipariş bitişleri: plan kapsamı, durum ve termin sonuçları.
- İş merkezi siparişleri: operasyon/ürün/sipariş detayları.
- Ciro: sipariş, plan ve sevkiyat anlamları ayrıştırılmalıdır.
- Karşılaştırma: alternatif plan ve kalite ölçümleri.
- Sipariş ilerleme: eski üretim kayıtlarından sipariş bazlı dağıtım.
- Birleştirme: aynı ürün talepleri için üretim partisi, üye siparişler ve etki analizi.
- Termin tahmini: yeni işin mevcut kapasiteye yerleşmesi.

Üst filtreler tüm sekmelere aynı biçimde uygulanmıyor. Birlikte sevk, üretim birleştirmesinden ayrı bir kavramdır. Günlük pilot, haftalık planın makine/takvim detayını üretir; veri eksikleri varken hazır kabul edilmemelidir.

### Stok & Rezervasyon
Serbest stok, rezervasyonlar, sipariş karşılama, stok girişleri ve sevkiyat. Otomatik tahsis önizlemesi ve onayı mevcut; mevcut rezervasyonları koruyarak termin sırasıyla dağıtır. Manuel tahsis/taşıma ve sevk/geri alma da vardır. Güncel rezervasyon kaydı tek başına ilk üretim niyetinin kalıcı tarihçesi değildir.

### Günlük İlerleme / MES
MES dosyası önizleme-onay, tekil detay kaydıyla tekrar yükleme kontrolü, makine/malzeme eşleştirme, yarımamul dengesi, bitmiş ürün stok girişi, dondurulmuş plan karşılaştırması, günlük kümülatif ve Excel çıktısı mevcut. Standart işçilik saati gerçek ölçülmüş MES süresi değildir.

Teslimat riski bu ekranın içindedir: müşteri/sipariş, stok, yarımamul, kalan operasyon ve tahminî tarihler için mevcut bir temel sunar. Buradaki serbest stok paylaşımı analiz içindir; rezervasyon kaydı oluşturmaz.

### Analiz
Duruşlar, kapasite kayıpları ve çevrim süresi analizi. Gerçek süre, standart süre ve miktardan türetilen saat birbirinin yerine kullanılamaz.

### Import / export
İş merkezi, istasyon/makine, üretim reçetesi, vardiya, haftalık iş gücü, personel, stok kartı, BOM, rota, geçiş kuralları, sipariş, eski üretim, duruş ve stok girişleri. MES importu ayrı akıştır. Şablon, API tipi, rapor ve kaynak anlamı birlikte değişmelidir. Excel yedeği tam veritabanı geri yüklemesinin eşdeğeri değildir.

### Kullanıcılar, owner ve ortak bileşenler
User/poweruser/admin/owner yetkileri; owner kayıt filtreleme ve silme kapsamları; veri güncelliği onayı; sağlık ve bütünlük kontrolleri. Yetki ve ilişkili kayıt temizliği yeni veri modeli eklenirken ayrıca gözden geçirilmelidir. Silme akışları canlı ortamda çalıştırılmadı.

## Kaynak kodda görülen kritik ayrımlar

1. **Gerçekleşme kaynakları farklı.** `orders.order_progress` eski `ProductionActual` verisini sipariş numarası veya FIFO ile dağıtır. `mes_progress` ve `delivery_risk` MES verisini kullanır. Stok rezervasyonu ise müşteriye fiilî tahsistir. Eski rapordaki FIFO payını gerçek müşteri rezervasyonu gibi göstermemeliyiz.
2. **Üretimden stoka iki yol var.** Eski üretim importunun stok senkronizasyonu ile MES'in bitmiş ürün giriş yolu ayrıdır. Aynı fiziksel üretimin iki kaynaktan yüklenmesi ve net ihtiyacın mükerrer düşülmesi senaryo testleriyle kontrol edilmelidir; canlı veride böyle bir hata olduğu henüz kanıtlanmış değildir.
3. **Brüt ve net ihtiyaç karışma riski.** Planlama stok/gerçekleşme sonrası kalan ihtiyacı kullanırken bazı bitiş/ilerleme raporları tam sipariş miktarının doğrudan operasyon saatlerini karşılaştırır. “Kısmi” etiketi düzeltilmeden önce ortak payda belirlenmelidir.
4. **Kapasite tek anlamda kullanılmıyor.** Haftalık kapasite, günlük ekip havuzu, ön kontroller ve termin hesabının varsayılan/eksik hafta davranışları birlikte ele alınmalıdır. LAZER örneğinin kök nedeni yalnızca personel ekranına bağlanamaz.
5. **Revizyon girdi özeti eksik kapsam taşıyor.** `plan_input_fingerprint.py` içinde MES, BOM, sevkiyat ve takvim girdileri görünmüyor; haftalık iş gücü ve eski üretim yalnızca açık iş merkezi filtresi varsa ekleniyor. Yeni hesap kaynakları revizyona taşınırken eski önizleme onayının geçerliliği de test edilmelidir.
6. **Toplu üretim raporlara yayılıyor.** Parti bağlantısını taşıyan ana sipariş üzerinden raporlama, diğer üye müşterilerin payını görünmez kılabilir. Plan satırı sayısını artırıp kapasiteyi çoğaltmadan müşteri payları gösterilmelidir.
7. **Zaman aşımı işlem iptali değildir.** İstemcinin 20 saniyelik iptali sunucudaki hesaplamanın durduğu anlamına gelmez. Birleştirme etki analizi için yerel kodda arka plan işi/polling yapısı var; otomatik plan için tasarım yapılırken bu mevcut yapı değerlendirilmelidir.
8. **Önizleme ve uygulama kapsamı doğrulanmalı.** Karşılaştırma ile otomatik uygulamanın `replace_existing` farkı; günlük pilotun haftalık kayıt sonrası hata davranışı ayrıca regresyon senaryosu gerektirir.

Bu maddeler kaynak inceleme bulgularıdır. Her biri ayrı canlı hata teşhisi veya uygulanması onaylanmış değişiklik değildir.

## Dokuz talebin bağımlılığa göre önerilen sırası

Mevcut FAZ 00–12 numaralarını yeniden kullanmamak için yeni paketler D0–D5 olarak adlandırılmıştır.

- **D0 — Ortak tanımlar ve kabul senaryoları:** brüt/net ihtiyaç; planlanan müşteri payı/güncel rezervasyon/sevk; MES/eski üretim; eksik/sıfır kapasite. İş kuralı değiştiren kararlar kullanıcıyla netleştirilir.
- **D1 — Kapasite ve yerleştirme (maddeler 1, 2, 3):** haftalık iş gücü önceliği, uyarılar, personel bağımlılığının kontrollü kaldırılması, operasyon zinciri ve haftalara bölünme. Revizyon, günlük pilot ve termin hesabı aynı pakette doğrulanır.
- **D2 — Gerçekleşme ve karşılama (maddeler 4, 7):** mevcut MES ve stok akışını esas alan kümülatif/kalan miktar ve saat; toplu üretimde müşteri payının korunması; mükerrer netleme kontrolleri.
- **D3 — Bitiş ve görünürlük (maddeler 5, 6, 7):** Gantt çoklu iş merkezi/ürün zinciri, WIP kod/ad, termin-durum sayıları, son operasyon bitişi ve Excel. Mevcut teslimat riskiyle ortak hesap kullanımı değerlendirilir.
- **D4 — Malzeme politikası (madde 8):** koşullu/strict davranışı, unknown/eksik veri açıklaması ve ekranlar arası tutarlılık. Mevcut anlamı onaysız değiştirilmez.
- **D5 — Uzun işlem yönetimi (madde 9):** ölçümle darboğaz tespiti, durum/progress, tekrar tıklamada mükerrer işin engellenmesi, hata ve uygulama bütünlüğü. Performans ölçümü D0'dan itibaren yapılabilir.

Her paketin kabulü ilgili backend testleri, frontend doğrulaması ve etkilenen ekran/API/Excel kontrollerini kapsamalıdır. Yalnızca değişen ekranın çalışması yeterli değildir.

## Başlangıç doğrulaması ve sınırlar

- Backend: izole SQLite üzerinde mevcut suite **194 passed, 2 skipped**; yeni skip/xfail eklenmedi.
- Frontend: mevcut seçim testleri **5 başarılı**; TypeScript + Vite üretim derlemesi başarılı. İlk derleme sandbox içindeki alt süreç engeli nedeniyle tamamlanamadı; izinli tekrar başarılı oldu.
- Canlı veri değiştiren planlama/import/rezervasyon/silme işlemleri bu incelemede çalıştırılmadı.
- Canlı arayüz gözlemi sınırlıdır; bütün butonların ve yetki rollerinin canlı uçtan uca testi yapıldığı iddia edilmez.
- PostgreSQL eşzamanlılık ve geri yükleme doğrulaması SQLite sonucuyla karşılanmış sayılmaz.
- Proje hafızasının bazı bölümleri MES ve son değişikliklerin gerisindedir. Önceki dokümanlardaki test sayıları güncel sonuç olarak kullanılmamalıdır.
- İnceleme başında mevcut olan takipli/takipsiz geliştirmeler korunmuştur. Bu belge uygulama kodu değişikliği değildir.

## Uygulama günlüğü — D1 ilk paket

Kullanıcı inceleme ve MES/rezervasyon ayrımı sonrasında uygulamaya başlanmasını onayladı. D0 tanımları yukarıda kayıtlıdır; D1–D5'in tamamı bitmiş değildir.

Yapılanlar:
- Ön kontrol artık ilk hafta yerine seçili ufkun bütün haftalarını kontrol eder. Eksik kişi/saat/gün girdisini iş merkezi ve hafta bazında bildirir. Açıkça girilen 0 eksik giriş sayılmaz; ihtiyaç olan merkezde kapasitesiz hafta ayrıca gösterilir.
- Eksik alanlarda mevcut varsayılan hesap davranışı korunur ve kullanıldığı kullanıcıya açıklanır. Personel bağımlılığının kaldırılması bu ilk pakette yapılmadı.
- Haftalık iş gücü ekranında giriş durumu görünür. Kapasite toplamı planlayıcıyla aynı tam gün tatil hesabını kullanır. Mevcut plan satırlarının iş gücü düzenlemesinde kendiliğinden değişmediği açıklanır.
- Revizyon parmak izi, “tüm iş merkezleri” seçiminde de haftalık girdileri kapsar; etkin günlük iş gücü kapasitesi ve planlama rezervini içerir. Bu değişiklik önceki bütün fingerprint kapsam eksiklerini kapattığı iddiası taşımaz.
- Ön kontrol isteği değiştiğinde önceki sonuç ve onaylar sıfırlanır; eski istek yanıtının yeni sonucu ezmesi önlenir.

Kabul senaryoları (`tests/test_weekly_labor_contract.py`):
- Personel kaydı olmadan 5 kişi × 5 saat × 5 gün = 125 saat.
- Birim süresi 1 saat olan 150 adetlik işin iki haftaya 125 + 25 yerleşmesi.
- Sonraki haftada sıfır kapasite, kısmi giriş ve hiç giriş olmamasının ayrı değerlendirilmesi.
- Bir günlük tatilde haftalık kapasitenin hem hesapta hem haftalık profilde 100 saate düşmesi.
- Tüm iş merkezleri seçiliyken haftalık iş gücü veya tatil değişikliğinin revizyon parmak izini değiştirmesi.

Paket doğrulaması: backend tam suite **198 passed, 2 skipped** (önceden var olan PostgreSQL ortam testleri); frontend **5 test başarılı**, TypeScript + Vite derlemesi başarılı. Yeni skip/xfail eklenmedi. `git diff --check` temiz. Canlı iş verisi değiştirilmedi; commit/push yapılmadı.

Sınırlar / sıradaki işler:
- Canlı LAZER verisindeki sonuç henüz kök neden olarak açıklanmış değildir; izole test haftalık temel çarpımın çalıştığını gösterir.
- WIP rotalarının ön kontrole eksiksiz taşınması, günlük ekip havuzu/termin hesabı ve personel geçişi D1'de beklemektedir.
- D2'deki MES/rezervasyon kaynak geçişi henüz uygulanmadı.
- Canlı arayüz kontrolünde localhost:5173 bağlantıyı reddetti. Bu yüzden bu paketin canlı ekran kabulü yapılmış sayılmaz; canlı backend yeniden başlatılmadı.

### Sonraki canlı arayüz doğrulaması

Uygulama açıldıktan sonra kullanıcı isteğiyle doğrulandı:
- LAZER H38–H45 detayında 5 kişi, 5 saat, 5 gün, 125 saat ve yeni “Tanımlı” göstergesi görünür.
- 8 haftalık ön kontrolde kapasite bölümü tamam durumunda.
- Yalnızca görüntüleme ufku 13 haftaya uzatıldığında H50 için eksik kişi/saat/gün girdileri ve kapasitesiz haftalar listeleniyor; LAZER de bu listede.
- Uyarı onayı verilmediğinde uygulama düğmesi devre dışı. Ön kontrol penceresinin görsel düzeni de incelendi.
- Ön kontrol kapatıldı, aralık 8 haftaya geri getirildi. Planlama uygulaması, rezervasyon veya iş gücü kaydı değişikliği yapılmadı.

Bu doğrulama önceki bağlantı engelini kaldırır. Tatil ve açık sıfır girdisi senaryoları izole testlerle doğrulanmıştır; canlı veri bu senaryoları üretmek için değiştirilmemiştir.

### D1 devamı — yarımamul, günlük ekip ve termin

- Kapasite ön kontrolü mamulün yarımamul operasyonlarını da BOM miktar katsayısıyla kapsıyor; tekil siparişler ve üretim partileri aynı genişletme yolunu kullanıyor.
- Mamul rotası mevcut olsa bile bağlı yarımamul kartı/rotası eksikse eksik rota listesine ekleniyor.
- Termin hesabı sıfır kapasiteli ilk haftada hemen başarısız olmak yerine ufuktaki sonraki haftaları arıyor; tüm ufuk kapasitesizse tarihsiz başarısız sonuç korunuyor. Günlük tatil kapasitesi de kullanılıyor.
- Günlük ekip havuzunda haftalık kişi girişi vardiya sayısına üstün; sıfır için yapay bir kişilik alt sınır kaldırıldı. Operasyon sonraki güne geçtiğinde havuz yeniden okunuyor.
- Personel bağımlılığını kaldırma adımı için boş haftalık kişi sayısının davranışı kullanıcıya soruldu: uyarı + sıfır kapasite (önerilen) veya uyarı + vardiya kişi sayısı. Bu seçim yanıtlanmadan fallback davranışı ve personel ekranı değiştirilmedi.
- Hedefli kontroller: haftalık sözleşme, ön kontrol ve günlük pilot 19 test başarılı; termin kontrolleriyle birlikte ayrı koşuda 13 test başarılı. Tam backend suite: **201 passed, 2 skipped**; yeni skip/xfail yok. Bu pakette frontend değiştirilmedi.
- Günlük pilotun eşzamanlı ekip havuzu düzeltmesi, tüm vardiya çakışmaları ve günlük verimli saat bütçesinin eksiksiz çözüldüğü anlamına gelmez. Bu nedenle D1 bütünü henüz kapatılmadı.


### D1 — eksik kişi sayısı: düzeltme veya açık sıfır kapasite onayı

Kullanıcı kararı: haftalık kişi sayısı boşsa önce uyarı ve düzeltme olanağı sunulacak; yalnızca açık onayla eksik haftalar sıfır kapasiteyle değerlendirilecek. Boş giriş kalıcı olarak sıfıra dönüştürülmeyecek.

- Ortak kapasite hesabı boş haftalık kişi sayısını eski personel/vardiya sayısından tamamlamıyor. Saat/gün varsayılanları korunuyor.
- Ön kontrolde her eksik hafta için mevcut haftalık iş gücü panelini açan Düzelt düğmesi var. Kaydetme ve düzenleme sırasında plan uygulama kapalı; düzenleme sonrası yeniden kontrol ve yeni onay gerekiyor.
- Haftalık alan kaydı ile yeniden okuma tamamlanana kadar başka alan kaydı engelleniyor.
- Otomatik planlama, karşılaştırmadan uygulama, toplu üretim panelinden planlama ve revizyon onayı aynı ön kontrolü kullanıyor.
- Backend, eksik iş merkezi/hafta listesine bağlı onay olmadan otomatik planı veya revizyonu uygulamıyor (409). Kapsamda yeni eksik hafta oluşursa önceki onay geçmiyor. Mevcut plan satırları bu engelde korunuyor.
- Revizyon ön kontrolü taslak değişikliklerini geçici olarak değerlendirip geri alıyor. Canlı haftalık giriş düzeltilirse revizyonun yeniden hesaplanması gerektiği açıklanıyor; mevcut fingerprint kontrolü korunuyor.
- Eski vardiya/personel varsayımına dayanan test hazırlıkları açık haftalık iş gücü kayıtlarıyla güncellendi; yeni skip/xfail eklenmedi.

Doğrulama: izole SQLite tam suite **204 passed, 2 skipped** (önceden var olan PostgreSQL ortam testleri); frontend **5 test başarılı**, TypeScript + Vite derlemesi başarılı. Onaysız engelleme, düzeltip tekrar planlama, kapsam değişikliğinde onayın geçersiz olması, boş kaydın korunması ve revizyon ön kontrolünün taslak değişikliğini kalıcılaştırmaması test edildi.

Canlı arayüz kontrolünde tarayıcı aracı iki kez zaman aşımına uğradı; bu yeni düzeltme akışının görsel kabulü tamamlanmadı. Canlı iş gücü/plan/sipariş verisi değiştirilmedi; commit/push yapılmadı. D1 bütünü kapatılmadı: eski personel/kaynak ekranlarının uyarlanması ile günlük pilotun verimli saat ve vardiya tutarlılığı kontrolleri sürüyor.


### D1 devamı — toplam haftalık kapasite ve ekran geçişi (17.09.2026)

Kullanıcı çoklu vardiya kuralını netleştirdi: haftalık kişi, kişi başı günlük verimli saat ve gün tüm vardiyaların toplamını ifade eder. İki vardiya olsa da 5 × 5 × 5 = 125 saat. Vardiya bazlı üretim/personel girişi istenmeyecek.

- Günlük/haftalık kapasite ve günlük ekip havuzu haftalık kişiyi vardiya sayısıyla çoğaltmıyor. Örtüşen ve ardışık iki vardiyada 125 saat/5 kişilik havuz regresyonları eklendi.
- İş merkezi ekranından eski personel kaynak seçimi, kaynak filtresi ve kapasiteyi temsil etmeyen personel toplamları kaldırıldı. İş merkezi satırı haftalık iş gücü sekmesiyle açılıyor. Personel menüsü/import kartı kaldırıldı; eski URL iş merkezlerine yönleniyor. Eski veritabanı kayıtları, API alanları ve yedek uyumluluğu korunuyor; eski capacity_headcount API alanı deprecated olarak açıklanıyor.
- Ekran ve Excel açıklamalarında boş kişi = eksik giriş ve tüm vardiyaların ortak toplamı anlatılıyor. Eski vardiya/personel kaynak alanlarının yeni hesabı değiştirmediği şablon notlarına eklendi.
- Günlük pilot makine boşluklarını ortak günlük iş gücü bütçesi (atıl rezerv dahil) ve eşzamanlı ekip sınırıyla kesiştiriyor. Kilitli segmentlerin harcadığı günlük kişi-saat düşülüyor. Ekip meşgulse sonraki boşluk değerlendiriliyor; hazırlık kesintisiz boşluğa sığmalı.
- Uzun işin tek makine aralığına sığması şartı kaldırıldı; günlere bölünme mümkün. Hazırlık süresinin hem hazırlık hem üretim olarak iki kez sayılması düzeltildi. Tam çevrim bitmeden ürün miktarı yazılmıyor. Bitişini tamamlamayan önceki operasyonun sonraki operasyonu tamamlanmış gibi serbest bırakması engellendi.

Doğrulama: tam suite **209 passed, 2 skipped**; son eklenen öncül testini ve Excel değişikliklerini kapsayan hedefli koşu **21 passed**. Frontend **5 passed**, TypeScript + Vite build başarılı. Üç import şablonunun açıklamaları bellekte oluşturulup doğrulandı; diff boşluk kontrolü temiz.

Canlı gözlem: 19 iş merkezi yükleniyor; LAZER 125 saat, H38–H49 haftalık satırları 5/5/5 ve Tanımlı. Personel menüsü/kaynak seçimi yok. Planlama ön kontrolünde yeniden yükleme sırasında sunucu zaman aşımı görüldü; sağlık sorgusu PostgreSQL db_ok=True. Küçük tarih aralığıyla tekrarda da tarayıcı etkileşimi zaman aşımına uğradı. Canlı plan, iş gücü veya sipariş verisine yazılmadı.

Kalan kabul sınırları: eksik hafta Düzelt→yeniden kontrol akışının canlı görsel kabulü tamamlanmadı. Günlük ayrıntılı pilotun eksik master verileri ve kısmi öncül üretimde çevrim bazlı aktarımın ayrıntılı miktar zinciri henüz hazır ilan edilmez; haftalık senaryo kuralları korunur. D1 ana haftalık kapasite/personel geçişi uygulandı; faz bütünü bu kabul sınırları kapatılmadan tamamlandı sayılmıyor. Commit/push yok.


### D1 devamı — çevrim aktarımı ve ön kontrol performansı (17.09.2026)

- Günlük pilotta kısmi öncül üretim, mevcut ortak geçiş miktarı kuralıyla ardıl operasyona aktarılıyor. Çevrimlerin gerçekten tamamlandığı zamanlar kullanılıyor; iki gün arasındaki boş süre üretim gibi sayılmıyor. Bekleme süresi, çevrim başına ürün miktarı, sıfır gecikmeli eşzamanlı başlangıç ve daha hızlı ardıl operasyon için regresyonlar eklendi. Önceki operasyonun tamamen bitmesini isteyen kural korunuyor.
- Korunan/kilitli segment miktarı yeniden üretilmiyor; kilitli segmentin elle belirlenen bitişi esas alınıyor. Seçim dışındaki iş merkezlerinin günlük segmentleri korunuyor. Canlı günlük çizelge üretilmedi.
- Ön kontrol aynı sipariş/parti ve yarımamul verisini istek içinde paylaşıyor. Canlı verinin salt okunur profilinde sorgu sayısı 6147 → 106, aynı profil yöntemiyle süre 16,01 → 5,19 saniye. 1240 talep ve 19 eksik hafta sonucu değişmedi. Profil olmadan ayrı servis ölçümü 2,01 saniye; HTTP kontrolü 200.
- Haftalık yük KPI hesabında üretim kaydı bulunmayan merkezler için tekrar tekrar plan/rota yükleme kaldırıldı. Üretim kaydı olan merkezlerde yüklenen plan satırları tekrar kullanılıyor; FIFO kapsamı ve gerçekleşme kaynağı değiştirilmedi. Canlı PostgreSQL salt okunur karşılaştırmada eski/yeni 113 hücre birebir aynı; servis 13,09 saniye/423 sorgudan 0,53 saniye/106 sorguya indi. Bu ölçümler servis süresidir, tarayıcı toplam açılış süresi değildir.
- Planlama açılışında görünmeyen sipariş sekmelerinin pahalı istekleri ertelendi; ilgili sekme/birlikte sevk açıldığında yükleniyor. İstemci zaman aşımı sınırı yükseltilmedi.
- Canlı ekranda 13 haftalık ön kontrol bu kez sonuçlandı: H50 eksikleri, düzeltme açıklaması ve Düzelt düğmeleri görünür; onaysız uygulama düğmesi devre dışı. İlk denemelerde tarayıcı otomasyonunun tıklama komutları zaman aşımına uğradı; aşağıdaki son kabulde bu engel geçici görünüm filtresiyle aşıldı. Canlı plan, sipariş, stok veya haftalık iş gücü değiştirilmedi.

Kapsam sınırı: bu paket günlük pilotun tanımlı ardışık operasyonlarının kapasite/çevrim akışını iyileştirir. Eksik makine/operasyon master verileri ile tüm yarımamul-mamul günlük bağımlılık grafiğinin saha kabulü tamamlandı iddiası yoktur. D2 MES/rezervasyon kaynak değişikliği başlamadı. D1 haftalık kapasite/personel geçişi ve düzeltme akışı kabulü tamamlandı. Günlük pilotun tüm yarımamul-mamul bağımlılık grafiği ve saha verisi kabulü ayrıca açık tutuluyor; bu kayıt tüm günlük pilotun hazır olduğu anlamına gelmez.


#### Son kabul ve test kanıtı

- Plan satırı görünümü geçici olarak Tahmin filtresine alındığında tarayıcı etkileşimleri yanıt verdi. 6481 satırın birlikte çizilmesiyle ilişkili ekran/otomasyon maliyeti D5 için inceleme notudur; tarayıcı profil ölçümü yapılmadan kesin kök neden ilan edilmez.
- 13 haftalık ön kontrolde LAZER H50 Düzelt açıldı; Kişi/Verimli saat/Gün girişleri düzenlenebilir, Eksik giriş etiketi görünür. Düzenleme sırasında uygulama düğmesi kapalı.
- Veri girmeden Düzenlemeyi bitir ve yeniden kontrol et kullanıldı. Eksik hafta korundu; onay kutusu boş, uygulama düğmesi kapalı. Yalnızca geçici onay kutusu işaretlenince uygulama düğmesi açıldı; düğmeye basılmadı. Pencere kapatıldı, ufuk 8 haftaya döndürüldü. Kaydetme/plan uygulama gibi canlı iş verisi yazımı yapılmadı. Kayıt düzeltip tekrar planlama senaryosu izole API testleriyle doğrulandı.
- Son kodla tam backend suite: **216 passed, 2 skipped**, 368,50 saniye. Skipler mevcut PostgreSQL ortamı gerektiren testler; yeni skip/xfail yok. Frontend **5 passed**, TypeScript + Vite build başarılı. Diff boşluk kontrolü temiz.
- Commit/push yapılmadı. D2 başlamadı.


### D1 / Faz 1 kapanışı — 17.09.2026

Durum: **anlaşılan kapasite ve yerleştirme geliştirme kapsamı tamamlandı**. Önceki günlüklerdeki açık teknik kabul maddelerini bu kapanış kaydı günceller.

Son kapatılan teknik eksik, günlük çizelgenin yalnız mamul rotasını dolaşıp haftalık planın yarımamul operasyonlarını atlamasıydı. Günlük plan artık programın mevcut BOM açılımını kullanır; yarımamul dalları birbirine yanlışlıkla bağlanmadan kendi operasyon sırası ve stok koduna özel senaryo kurallarıyla işlenir. İlk mamul operasyonunun beslenmesi mevcut ortak BOM oranı hesabından türetilir. İki A + bir B gereken mamulde dört A en fazla iki mamulü besler. Eksik bağlı rota veya tamamlanmamış öncül, üretim tamamlanmış gibi değerlendirilmez.

- Günlük öncül miktarları seçim dışı iş merkezlerinin haftalık planını da salt okunur bağlam olarak kullanır; bu merkezlerin günlük segmentlerini yeniden yazmaz.
- Çevrim çıktı olayları tamamlanma zamanına göre sıralanır. Kilitli segmentler, mevcut gerçekleşme kredisi ve tüketilmiş montaj miktarı korunur; yeni gerçekleşme kaynağı veya müşteri dağıtım kuralı tanımlanmadı.
- Günlük API uyarılarında operasyon kimliği ve stok kodu yer alır. Planlama sonucunda günlük eksik kaynaklar ve kalan miktarlar Türkçe olarak görünür; yalnız haftalık başarı mesajıyla günlük eksiklik gizlenmez.

Kapanış kanıtı:
- Tam backend suite: **222 passed, 2 skipped**, 89,06 saniye; yeni skip/xfail yok. 2 skip mevcut PostgreSQL ortam testleridir.
- Frontend: **5 passed**, TypeScript + Vite build başarılı. İlk sandbox derlemesindeki esbuild alt süreç engeli izinli derlemeyle aşıldı.
- Haftalık otomatik plan → günlük yarımamul/mamul → Gantt entegrasyonu test edildi; günlük pilot dosyasında 23 test başarılı.
- Boş hafta düzeltme/yeniden kontrol/açık sıfır onayı canlı ekran kabulü önceki son kabul bölümünde kayıtlı. Bu paketin günlük plan uygulaması canlı veride çalıştırılmadı; yeni sonuç paneli TypeScript/build ile doğrulandı.
- Sağlık API'si PostgreSQL için db_ok=true. Diff boşluk kontrolü temiz. Canlı iş verisi, sipariş, stok, rezervasyon ve planlar değiştirilmedi; commit/push yok.

Faz 1'i açık tutmayan dış veri koşulları: gerçek makine/operasyon master tanımlarının tamamlanması ve günlük pilotun saha denemesi, TEST_PG_URL ile PostgreSQL ortam testleri. Teknik geliştirme tamamlanması bu verilerin hazır olduğu iddiası değildir. Çok büyük plan tablosunun ekran performansı D5 incelemesinde; MES/rezervasyon gerçekleşme kaynağı dönüşümü D2'de kalır. D2 henüz başlamadı.


### D2 başlangıcı — kaynak geçişi ve revizyon koruması (17.09.2026)

Kullanıcı Faz 2'ye geçilmesini onayladı. Canlı PostgreSQL salt okunur kontrolünde 18 eski üretim, 0 MES kaydı; import kaynaklı 5409 stok girişi ve progress kaynaklı 100 adetlik 1 stok girişi bulundu. Bu sayılar kaynakların gerçekten aynı fiziksel üretimi içerdiğini kanıtlamaz; kayıt eşleştirmesi yapılmadı. Geçişin MES yüklendikten sonra mı, yoksa üretim göstergeleri boş kalacak şekilde hemen mi etkinleşeceği kullanıcıya soruldu. Yanıt beklenirken eski kayıt/stok silinmez ve gerçekleşme kaynağı değiştirilmez.

Tamamlanan bağımsız hazırlık: plan revizyonu girdi parmak izi MES detay/eşleme/miktarını, sevkiyatı, BOM'u, bağlı WIP rotalarını ve parti/üye miktarlarını kapsıyor. Ortak MES havuzu seçili merkezin dışından beslendiği için MES defteri yalnız iş merkezi filtresiyle daraltılmıyor. Audit kullanıcı değişimi tek başına plan girdisi değişikliği sayılmıyor. Eski revizyon taslakları bu kapsam değişikliğinde yeniden hesaplanmalıdır.

Hedefli kontrol: yeni fingerprint regresyonu ve mevcut revizyon testleri 9 passed. Tam suite sonucu takip kaydına eklenecek. API şekli ve frontend değiştirilmedi. D2 tamamlanmış veya MES kaynağı devreye alınmış değildir.


D2 uygulamasında birlikte ele alınacak doğrulanmış kod noktaları:
- `remaining_work.produced_qty_map` eski üretim ve MES planlama kredisini birlikte ekliyor; bunlar müşteriye gerçek tahsis değildir. Kaynak geçişi açık politika ile yapılmalı.
- `orders.order_progress` yalnız eski üretimin sipariş/FIFO dağılımını gösteriyor. Yeni raporda planlama payı, MES üretimi ve rezervasyon/sevk karşılaması ayrı anlam taşımalı; mevcut stok dağıtım butonu yeniden yazılmamalı.
- `required_qty_for_batch`, yalnız ana siparişin net ihtiyacını parti oranıyla çoğaltıyor. Üye siparişlerin rezervasyon/sevk miktarları farklı olduğunda bu yaklaşım her üyenin gerçek kalan talebini yansıtmıyor. D2 testleri farklı üyelerde tahsis/taşıma ve toplam net ihtiyaç korunmasını kapsamalı; ana sipariş anahtarındaki MES parti kredisinin iki kez düşülmemesine dikkat edilmeli.
- MES bitmiş ürün stok kaydı ile eski `sync_progress_receipts` ayrı yazım yollarıdır. 100 adetlik mevcut progress stoğunun aynı fiziksel üretim olup olmadığı bilinmiyor; otomatik silme/dönüştürme yapılmayacak.


### D2 devamı — MES zaman aşımı ve kaynak ayrımı (17.09.2026)

Kullanıcı kararı: MES verisi yüklendikten sonra geçiş etkinleştirilecek. Önceki karar-bekleniyor kaydı kapanmıştır. `production_source` varsayılanı `legacy`; örnek ortam dosyası bu durumu açıkça belirtir. Canlı `.env` değiştirilmedi. `/api/mes/source-status` kaynağı, MES varlığını ve otomatik geçiş olmadığını bildirir; MES ekranı da bunu açıklar.

Zaman aşımı: ekran görüntüsündeki sorun iki rapor isteğindeydi; gerçek Excel dosyasının okunmasından kaynaklandığı kanıtlanmadı. MES planı hafta başına ve satır başına tekrar yükleniyordu. Haftalar toplu yükleniyor, operasyon ve sipariş stokları eager load ediliyor; import sırasında donmuş plan kaydı da aynı toplu okumayı kullanıyor. Teslimat riski tüm stok kataloğunu yüklemek yerine açık siparişlerin mamul ve bağlı yarımamul kapsamını okuyor. Eski üretim eşlemesinde WIP indeksi yalnız gerektiğinde kuruluyor. Zaman aşımı süresi yükseltilmedi.

Salt okunur aynı canlı veri profilinde:
- İlerleme: 909 → 70 sorgu; 1,483 → 0,899 saniye.
- Teslimat riski: 292 → 92 sorgu; 11,221 → 4,643 saniye.
- Her iki eski/yeni yanıtın SHA256 özeti aynı; iş sonucu değişmedi.
- Son eşzamanlı HTTP doğrulaması (5173 proxy, 4 hafta): ilerleme 200 / 1,51 sn, teslimat riski 200 / 3,86 sn. PostgreSQL db_ok=true. Tarayıcıda zaman aşımı yok, MES özet kartları 1448,4 saat plan ve 0 MES üretimi gösteriyor. Tam DOM ağacı aracı zaman aşımına uğrasa da ekran görüntüsü ve sınırlı DOM okuması sonucu doğruladı.

D2 kod hazırlığı:
- Planlama kredisi ya eski üretimden ya MES'ten gelir; ikisi toplanmaz. MES ekranı her zaman MES'i gösterir. MES modu boşsa eski kaynağa sessiz geri dönüş yok.
- MES miktarı güncel kesin plan satırlarıyla bir kez eşleşir; aynı miktar iki haftaya tekrar yazılmaz. KPI/Gantt/üretim ilerlemesi ortak ölçümü kullanır. Bu plan eşleşmesi rezervasyon değildir; MES'in donmuş haftalık referans raporu ayrı karşılaştırmadır. Güncel eşleşme mevcut yakın ufuk yaklaşımıyla 4 haftadır; geçmiş plan sahipliği snapshot'ı olarak sunulmaz.
- Sipariş raporu MES modunda rezervasyon ve sevkiyatı müşteri karşılaması olarak gösterir. Planla eşleşen miktar/saat ve partinin başlangıç üye miktarından oransal referans payı ayrıdır. Stok başka üyeye ayrıldığında pay değişmez. Rapor tarihi MES kayıtlarını sınırlar; müşteri karşılaması güncel bakiyedir. Excel açıklama sayfası ve yeni kolonlar aynı anlamı taşır.
- Parti net ihtiyacı ana sipariş oranından değil her üyenin kendi kalan talebinden hesaplanır. Parti hazırlık saati müşteri raporunda üyeler kadar çoğaltılmaz. MES üretim kredisiyle rezervasyon aynı miktarı iki kez azaltmaz; mevcut stok dağıtım düğmeleri korunur.
- MES modunda eski üretimden otomatik stok türetimi durur; mevcut progress/import stokları silinmez veya dönüştürülmez. Kaynak politikası da revizyon fingerprint'ine dahildir.

Kanıt: tam izole backend suite 231 passed, 2 mevcut PG skip (135,77 sn). Ardından güncel sevk/geçmiş MES tutarlılığı ve kaynak fingerprint testi dahil hedefli 39 passed; kaynak/KPI/Gantt müşteri ayrımı dosyası 9 passed. Frontend 5 passed; TypeScript/Vite build başarılı. Diff boşluk kontrolü temiz. Yeni skip/xfail yok.

D2 canlı kapanışı henüz yapılmadı: gerçek kullanıcı Excel dosyası önizleme/ithal sonucu ve eski stoklarla fiziksel örtüşme doğrulanmadan kaynak etkinleştirilmez. MES ve stok importlarının aynı fiziksel üretimi içerip içermediği koddan çıkarılamaz; 100 adet eski progress stoğu korunur. Mevcut planın yeniden eşlemesi geçmişte kimin için üretildiğinin değişmez kaydı değildir; bu ayrım saha kabulünde kontrol edilecek. Canlı iş verisi yazılmadı, commit/push yapılmadı.


### MES gerçekleşmesinde rota dışı kaynak kabulü — kullanıcı kararı, 17.09.2026

MES üretimin nerede yapıldığı konusunda esas alınır. Planlamada tanımlı rota/makine kısıtları korunur. Tanımlı alternatif olmayan veya katalogda hiç olmayan makine, malzemenin rota operasyonu tutarlı biçimde belirlenebiliyorsa üretimin reddine neden olmaz. Miktar ve standart işçilik planlanan rota merkezine kredi yazılır; bu merkezin nominal kapasitesi/kişi-saat girişi azaltılmaz, kalan plan yükü azalır. Gerçek makine kodu kayıtta, gerçek iş merkezi (biliniyorsa) eşleştirme bilgisinde korunur. `resource_deviation` önizlemede açıklanır.

Uygulama: merkez eşleşmesi yoksa malzemenin merkezden bağımsız BOM/rota adayları değerlendirilir. Mamul BOM rotasının önceliği korunur; standart süre, operasyon anahtarı ve tüketim bileşimi çelişiyorsa rastgele aday seçilmez. Eksik BOM/rota, geçersiz tüketim katsayısı ve eksik standart süre ayrı engellerdir; bunlar yalnız alternatif makine kullanımından kaynaklanan uyarılar değildir. Dosya kimliği, tarih ve miktar doğrulaması korunur.

Salt okunur canlı tanım kontrolü: 5005338-10/HP-82 → KALIBRE/PRESHANE 2 (gerçek PRESHANE 1), 5004186-17/HP-75 → AGIZ KIVIRMA/PRESHANE 1, 5004186-68/HP-75 → 2.AGIZ KIVIRMA/PRESHANE 1, 5004318-16/HP-03 → AGIZ FORMA/PRESHANE 3 artık eşleşiyor. 5001962-23/DTM-05 için bağımsız rota operasyonu bulunamadı; bu ekran satırının miktarı 0 olduğu için tek başına eksik üretim adedi oluşturmaz.

Yeni regresyonlar: farklı merkez ve tanımsız makine için plan satırı/KPI/kalan iş kredisi, rotanın değişmemesi, yeniden yükleme idempotansı, bitmiş stok girişinin tekilliği, çelişkili sürede tahmin yapılmaması. Hedefli MES + D2 kaynak testi 34 passed. Kaynak geçişi canlıda hâlâ legacy; mevcut önizleme kapanıp dosya yeniden seçilerek yeni eşleşme alınmalı. Canlı veri ithal edilmedi ve rota değiştirilmedi.

Son doğrulama: tam backend suite **236 passed, 2 mevcut PostgreSQL skip**, 82,62 sn; frontend TypeScript/Vite build başarılı. Canlı 5173 proxy üzerinden yalnız önizleme isteği HTTP 200 / 5,59 sn: 5005338-10/HP-82 → KALIBRE, plan PRESHANE 2, gerçek PRESHANE 1, resource_deviation=true, unresolved=0. İçe aktar çağrısı yapılmadı; stok/üretim kaydı yazılmadı. PostgreSQL db_ok=true.


### MES yarımamul rotası / üst mamul BOM bağlantısı — 17.09.2026
Kullanıcının 5001962-23 örneği için düzeltme yapıldı. Yarımamul kartının yerel BOM'u boş olsa da üst mamulün pozitif recipe_seq içeren BOM operasyon satırı varsa rota elenmez. Bağımsız yarımamul rotasının tüketimi bu üst BOM bağlamlarından okunur; birden fazla mamul aynı tanımı veriyorsa tek kredi, çelişkili tüketimde unresolved korunur. Sipariş bulunması koşul değildir. İlk operasyon için sahte öncül tüketim üretilmez. Rota, ürün veya canlı stok kayıtları değiştirilmedi.
Kanıt: MES/D2 kaynak 37 passed, kalan iş/netleme/teslimat riski 22 passed; toplam 59 hedefli test. Sayısal stok kodu fixture'larıyla ilk operasyon, önceki aşama katsayısı, çoklu mamul uyumu/çelişkisi ve yeniden yükleme test edildi. Frontend/API sözleşmesi değişmedi. Backend yeniden başlatıldı; canlı proxy önizlemesi HTTP 200, unresolved=0, 5001962-23 → DIKIS KAYNAK, 0.2512 saat/adet (904.32 saniye), aday mamul 6010758. Yalnız önizleme çalıştırıldı, ithal yapılmadı. Önceki açık 'rota yok' tespiti bu kayıtla düzeltilmiştir. Mevcut önizlemeyi kapatıp dosyayı yeniden seçmek gerekir. Commit/push yok.


### MES montaj yarımamul çıkışı — 5800090-11 senaryosu, 17.09.2026
5800090-11, 47 mamulde component=source_wip=5800090-11 / recipe_seq=0 montaj bağlantısı olarak bulunuyor; aynı dalın önceki aşaması 5800090-03 / recipe_seq=10. Bağımsız rotalar ETEK KESME / PRESHANE 2 / 48 saniye içeriyor, yerel BOM'ları boş. Önceki parent-BOM düzeltmesi sadece pozitif recipe_seq operasyon satırlarını indekslediğinden bu son yarımamul çıkışını kaçırıyordu.
Genel çözüm: is_wip_asm_link ile tanınan montaj yarımamul çıkışları da parent BOM bağlamına dahil edilir. Son aşamanın önceki tüketimi, önceki BOM miktarı / montaj bağlantısı miktarı ile birim çıkışa normalize edilir. Örneğin 2 çıkış için 4 öncül ve 4 çıkış için 8 öncül aynı 2:1 kuralıdır; çoklu mamul kayıtları üretimi çoğaltmaz. Çelişkili oranlar unresolved kalır. Stok koduna özel koşul yok, sipariş varlığı şartı yok.
Doğrulama: 47 hedefli MES/kaynak/teslimat riski testi başarılı. Farklı mamul kullanım miktarlarında aynı birim oran, çelişki, ortak havuza tek giriş/tüketim ve yeniden yükleme testi eklendi. Backend otomatik reload eski worker'da takıldığından yeniden başlatıldı. Canlı 5173 proxy önizlemesi HTTP 200 / unresolved=0; ETEK KESME, 0.0133333333 saat/adet, tüketim 5800090-03:1, aday mamul 47. Önizleme dışında canlı iş verisi yazılmadı; commit/push yok. Frontend değişmedi.


### MES önizleme zaman aşımı — gerçek dosya performans düzeltmesi (17.09.2026)
Kullanıcı zaman aşımının dosya seçiminden sonra, önizlemeden önce olduğunu netleştirdi. Backend/PG sağlıklıydı. Downloads/Uretim_Kayitlari_Raporu_17 Eylül 2026 14_40.xlsx dosyası yalnız okunarak ve canlı önizleme API'sine gönderilerek incelendi; ithal/onay çağrısı yapılmadı.
Ölçülen darboğaz: Mapper her istekte tüm Item/BOM/rota kataloğunu ORM nesneleri olarak yükleyip indeksliyor, tekrarlı malzeme-makine satırlarında aynı aday/tüketim hesaplarını tekrar yapıyordu. Gerçek dosya cProfile: 12,797 sn, 214 sorgu, yaklaşık 30 milyon çağrı; bunun 8,271 sn'si mapper kurulumu. Bu ölçüm kullanıcıdaki zaman aşımının tek başına birebir yeniden üretimi değildir; eski 20 sn istemci eşiğini geçen olayın zaman damgalı süre kaydı yoktur, ancak yavaş yol doğrudan saptandı.
Düzeltme: dosyadaki kodların rota sahipleri, doğrudan ürünleri ve hem doğrudan hem rota sahibi üzerinden bağlı bütün mamul BOM bağlamları yüklenir. Sipariş filtresi uygulanmaz. Kod normalizasyonu korunur. Makine/merkezler eager load edilir. Aynı malzeme-makine için eşleme yalnız istek içinde paylaşılır; sonraki önizlemede rota değişikliği tekrar okunur. Global eskimiş cache kullanılmaz. Önizleme tokenı ve importun yeniden doğrulaması korunur.
Aynı veri/dosya profili: 4,094 sn / 20 sorgu; tüm yanıt ve tokenın SHA256 özeti eski sonuçla birebir aynı. 638 yeni, 14 unresolved (624 eşleşen) değişmedi. 41 MES/kaynak testi başarılı; yeni regresyonlar tekrarların tek hesaplanması, sonraki istekte süre değişikliğinin görünmesi ve kapsam daraltırken dolaylı mamul adaylarının korunması.
Ek işletim düzeltmesi: Windows otomatik reload önceki turlarda değişikliği algılayıp eski worker'da kalmıştı. start_backend.ps1 varsayılan olarak reload olmadan çalışır; geliştirmede istenirse -Reload verilir. Bundan sonra kaynak değişikliğinin lokalde devreye alınması için bu script ile açık yeniden başlatma gerekir; Vite HMR değişmedi. Script yeniden başlatma/health kontrolüyle doğrulandı.
Canlı 5173 proxy, gerçek dosya ve eşzamanlı iki rapor: önizleme HTTP 200 / 4,94 sn, MES ilerleme 1,83 sn, teslimat riski 5,69 sn. Ardışık tekrar önizleme HTTP 200 / 2,72 sn. 20 sn istemci sınırı yükseltilmedi. Canlı üretim/stock/plan yazılmadı, gerçek Excel değiştirilmedi, commit/push yok. Frontend değişmedi.


### Tanımsız MES kodlarını serbest stok olarak saklama — 17.09.2026
Kullanıcı, stok/BOM/rota tanımlarında hiç olmayan MES kodlarının üretim veya siparişe bağlanmadan serbest stokta tutulmasını, onay öncesinde açık uyarı verilmesini istedi. Salt okunur kontrol: 5012321-13, 5012321-32, 5012323-13, 5200481-21 ve 5200482-21 için Item/BOM/rota yok. Yeni açıldıkları kanıtlanamaz, tanımsız oldukları doğrulandı. 5800019-02 tanımlı ve çelişkili olduğundan bu politikaya dahil edilmez.
Genel uygulama: hiç tanımı olmayan kod status=free_stock / kind=unassigned olarak önizlenir. Miktar mevcut MesDetail defterinde tekil detay ID ile saklanır; stok kartı, rota, süre veya sahte BOM üretilmez. Ayrı StockReceipt yazılmadığından aynı miktar iki depoya çoğaltılmaz. GET /api/mes/free-stock kod bazında bakiye, detay kimlikleri, tarih ve gerçek makineyi sunar. Stok & Rezervasyon / Serbest stok sekmesinde tanım bekleyen MES stoğu ayrı görünür. Mamul rezervasyon motoruna, MES WIP dağıtım havuzuna veya kapasite gerçekleşmesine katılmaz. MES Excel raporuna ayrı serbest stok sayfası eklendi.
İdempotans: aynı dosya miktarı artırmaz; detay miktarı düzeltmesi bakiyeyi günceller. Tanımlar kısmen açılırsa kabul edilmiş serbest stok korunur. Tanımlar tamamlandıktan sonra aynı MES dosyası yeniden önizlenip onaylanınca aynı detay mapped olur; serbest stok listesinden çıkar ve normal havuza yalnız bir kez girer. Yeni açılan fakat eksik tanımlı, daha önce serbest stok kabul edilmemiş kodlar otomatik bu politikaya sokulmaz. Çelişkili operasyon/süre/tüketim unresolved kalır.
Doğrulama: 62 ilgili MES/kaynak/stok/teslimat riski testi başarılı, TypeScript/Vite build başarılı. Backend yeniden başlatıldı. Gerçek 638 satırlık dosya canlı proxy önizlemesi HTTP 200 / 2,44 sn: 6 free_stock, 8 unresolved; serbest kodlar yukarıdaki beş kod + 5910806-13. Kalanlar 5800019-02 ve 5800067-11. Canlı serbest stok endpoint'i 0 kayıt: önizleme dışında import/onay yapılmadı. Gerçek Excel değiştirilmedi, canlı master/stock/plan yazılmadı, commit/push yok. Önizlemeyi yeniden açmak gerekir.


### Kullanıcı düzeltmesi: operasyonlar arasında adet korunumu — 17.09.2026
Kullanıcı 5800019-02 Tavlama için iki giren/iki çıkan kuralını netleştirdi; önceki BOM oranı açıklaması yanlış aşama seçimine dayanıyordu. 5800019 ve 5800019-02 rotaları SIVAMA -01 → TAVLAMA -02, 186,24 sn. 6012095/6012265 BOM'undaki -19 bileşen miktarını MES dönüşüm katsayısı saymak yanlıştı. Geçmişte farklı zamanlarda toplam 4 giriş/4 çıkış beyanı bu kayıtlardan tarihsel olarak doğrulanmadı; BOM snapshot'ından üretim dengesi sonucu çıkarılmaz.
Genel düzeltme: bağımsız yarımamul rotasında tanımlı önceki operasyon varsa aynı parçaların sonraki operasyonuna geçiş 1:1 tüketimdir. Mamul başına bileşen miktarı bu operasyonun dönüşüm katsayısı değildir. Montaj çıkışı aynı BOM'da başka dalda bileşen olarak da geçiyorsa o kullanım kendi üretim dalını geçersiz kılamaz. Mamul montaj tüketimi/BOM katsayıları korunur; tüm BOM katsayıları topluca 1 yapılmadı. Açık rota öncülü yoksa mevcut BOM bağlamı kullanılır, gerçek rota/süre çelişkisi gizlenmez.
53 MES/kaynak/teslimat riski testi başarılı; 4 adet -01 → 4 adet -02, -19 bileşeninin yanlış düşülmemesi, başka dalda tekrar kullanım ve farklı mamul adetlerinin rota dönüşümünü değiştirmemesi test edildi. Backend yeniden başlatıldı. Gerçek dosya canlı önizleme HTTP 200 / 2,38 sn: 5800019-02 (96 adet) ve 5800067-11 (365 adet) mapped; sırasıyla -01:1 ve -03:1 tüketim.
Yeni görünür olan ayrı belirsizlik: 5800011-02 için kök 5800011 rotasında önceki aşama -01, 5800011-02 kartı rotasında -19. Yeni rota-öncelikli kontrol bu gerçek alternatif öncül çelişkisini yakalıyor; 12 kayıt unresolved, 6 free_stock. Bu kod için seçim/onay alınmadı, uydurma aşama seçilmedi. Toplam önizleme 638 yeni. Canlı ithal/onay, master değişikliği ve commit/push yok.


### Kümülatif MES yarımamul stok defteri — 17.09.2026
Kullanıcı onayı: açılış stoğu sıfırdır; gerçek stokla canlı geçiş revizyonu daha sonra yapılacak. Eski yarımamul stok dosyası MES üretimine otomatik eklenmez. Önceki serbest stok politikasını bu karar genişletir: tanımsız veya eksik tanımlı WIP üretimi de ortak miktar havuzuna girer ve bilinen sonraki operasyon tüketiminde kullanılabilir.

`mes_inventory.py` tek MES defterinden üretim girişleri, bilinen tüketimler, tarih dahil kümülatif bakiyeler ve planlamada kullanılabilir miktarı türetir. Gün değişiminde sıfırlanmaz. Aynı günün bütün girişleri tüketimlerden önce netleştirilir; Excel satır sırası sonucu değiştirmez ve saat bazlı sıralama iddiası yoktur. MesDetail kimliği aynı kaldığından yeniden yükleme çoğaltmaz, düzeltme mevcut hareketi değiştirir. Ayrı stok giriş kopyası yazılmaz.

Operasyon ve standart süre tutarlı, tüketim öncülü çelişkiliyse üretim ve işçilik kabul edilir; fiziksel tüketim uydurulmaz. Olası öncül miktarları stoktan düşülmeden planlama kullanılabilirliğinden ayrılır (aynı stok ikinci kez iş azaltmasın). Üretilen çıktı başka operasyon tarafından aynı dosyada/günde tüketilebilir. Operasyon/süre bilinmiyorsa miktar korunur, hayali işçilik yazılmaz. Bitmiş ürün rezervasyon/sevk akışı değişmez; stok havuzu müşteri rezervasyonu oluşturmaz.

Stok & Rezervasyon → Yarımamul stok hareketleri: tarih/kod filtresi, üretim-tüketim-kalan-ayrılan-kullanılabilir miktarlar, detay ID ve makine ile hareket listesi, belirsiz tüketim alternatifleri ve Excel dışa aktarımı. `/api/mes/inventory` ve `.xlsx`; açılış zero_confirmed. Eski `/free-stock` artık sonraki bilinen tüketimleri düşen ortak bakiye gösterir. MES önizleme ve ilerlemede tüketim belirsizliği ayrıca uyarılır. Kalan iş ve teslimat riski aynı kullanılabilir havuzu kullanır; gerçekleşen işçilik yalnız üretimden bir kez hesaplanır, tüketim hareketi işçilik yazmaz.

Doğrulama: tüm backend suite 252 passed / 2 mevcut PG skip (yeni skip yok); TypeScript/Vite build başarılı. Regresyonlar: beş gün birikim/sonraki hafta tüketim, aynı gün beş operasyon ve ters Excel sırası, belirsiz öncülün geçici ayrımı ve düzeltmesi, serbest stoktan aynı gün tüketim, Excel/API, tekrar ithalde miktar ve kapasite idempotansı. Testler izole SQLite.

Backend lokalde yeniden başlatıldı; health PostgreSQL db_ok=true. Gerçek 638 satırlık MES dosyası sadece önizlendi: 3,08 sn / HTTP 200; unresolved=0, free_stock=6, pending_consumption=18. Bunların 12'si 5800011-02: 299 adet, TAVLAMA standart işçiliği kabul, -01 veya -19 tüketimi belirsiz. Diğer 6'sı tanımsız kod. 5800019-02 ve 5800067-11 bilinen 1:1 tüketimle eşleşiyor.

Canlı MesDetail henüz yok; production_source=legacy korunuyor. Kullanıcının önce MES yükleyip ardından kaynak geçişini etkinleştirme kararı geçerlidir. Canlı import/onay, master/stok/plan yazma veya Git commit/push yapılmadı. Kullanıcı önizlemeyi yenileyip onayladığında bu defter dolacak; genel kapasite planında MES kaynağı devreye alma ayrı geçiş adımıdır.


### MES önizleme filtrelerinin çakışması — 17.09.2026
Kök sebep: serbest stok kayıtlarında tüketim belirsizliği için consumption_status=pending korunuyor; UI filtresi ve önizleme sayacı status=mapped koşulu olmadan bu alanı kullandığı için free_stock kayıtları iki listede görünüyordu. Hesaplama metadatası ile önizleme inceleme grubu birbirine karışmıştı.
Çözüm: mes.preview_category tekil inceleme grubunu belirler. mapped+pending → pending, free_stock → free_stock, unresolved → unresolved. Önizleme API satırında preview_category döner; pending_consumption sayacı aynı sınıflandırmayı kullanır. Frontend filterMesPreview API grubuna göre filtreler; yeni/güncellenen filtresi bağımsız eylem filtresidir. Gösterilen/toplam kayıt sayısı eklendi, uyarı metni grupları açıklar. Stok hareketleri, tüketim belirsizliği, olası stok ayırma, standart işçilik veya kaynak geçişi değiştirilmedi.
Doğrulama: 53 ilgili backend testi, 7 frontend testi ve TypeScript/Vite build başarılı. Backend lokalde yeniden başlatıldı. Gerçek 638 satırlık dosya canlı önizleme API'si ve ekranın kullandığı TypeScript filtre fonksiyonuyla kontrol edildi: pending=12 (5800011-02), free_stock=6 (5012321-13,5012321-32,5012323-13,5200481-21,5200482-21,5910806-13), iki listenin kesişimi=0; sayaçlar listelerle aynı. İçe aktarma yapılmadı. Tarayıcıda dosya seçici otomasyonu zaman aşımı verdiğinden görsel filtre seçimi tamamlanamadı; doğrulama gerçek API yanıtı + UI'nin gerçek filtre fonksiyonu üzerinden yapıldı. Önizleme ekranını yeniden açmak/yeni API yanıtı almak gerekir. Commit/push yok.


### Kaynakta olmayan ERP kök kartının MES rotasını gölgelemesi — 18.09.2026
Önceki “gerçek alternatif rota çelişkisi” teşhisi düzeltildi: kaynak BOM.xlsx içinde 5800011 çıplak kodu yok; 661 mamulde 890 adet -01/-19/-02 dalı var. Veritabanındaki 5800011 kartı (5731), 231571/231572 operasyonlarıyla kalmış eski bağlantısız ERP kaydıdır; oluşturulduğu tarih/işlem kesin kanıtlanamadı. Canlı salt okunur taramada dış BOM referansı olmayan, rotalı 5048 ERP çıplak kök kartı bulundu; hiçbirinin Order veya PlanLine bağlantısı yok. Bu sayı tümünün hayali olduğunun tek başına kanıtı değildir.

MES genel düzeltmesi: aynı çıktıya ait mamul BOM'una bağlı rota bulunduğunda, ERP grubundaki çıplak kök sahibinin dış BOM referansı veya siparişi yoksa eski rota aday dışı kalır. Gerçek bağlı alternatifler korunur; makine tercihinden önce uygulanır. Tek başına duran bir rota otomatik atılmaz. Kök kart veya geçmiş plan silinmez; işlem yalnız aday seçimidir. Dış referans/sipariş kanıtı önizleme başına toplu iki sorguyla alınır, malzeme başına sorgulanmaz. İlk doğrulamadaki satır başına sorgu darboğazı giderildi.

BOM aktarımı: reconcile_operation_labels aynı tam stok kodunun kaynak dosyadaki tutarlı operasyon adını kullanarak eksik etiketi tamamlar; mevcut operasyon kodu ve süre uyumu şarttır. Makine, miktar, süre kopyalanmaz; normal sıfır/boş süreç alanlı malzeme satırı malzeme kalır. Güvenilir tanım yoksa master yazımından önce hata ile durur. Filtreli mamul ithalinde kaynak tanımlarının tümü referans alınır, kapsam dışı satır hataları hedef aktarımı engellemez. Kaynak dosya değiştirilmez; uyarılar mamul/SIRA/kodu gösterir.

Gerçek dosya kaynak kontrolü: 229 eksik operasyon adı tutarlı kaynak örneğiyle tamamlanabilir; 76 satır güvenle tamamlanamaz (çıktı outputs/5800011-inceleme/bom-operasyon-kontrolu.txt). 6007573 SIRA 134927, 6012099 SIRA 135051/135063 ara yıkama etiketleri güvenle tamamlanır; parser -01 → -19 → -02 zincirini korur. Bu turda canlı BOM yeniden ithal edilmedi. Diğer 76 kaynak satırı toplu yeniden ithalden önce değerlendirilmelidir.

Doğrulama: tam backend paketi 262 passed/2 mevcut skip. Son toplu sorgu performans düzenlemesinden sonra ilgili 46 MES/stok testi ayrıca geçti. Frontend değişmedi. Backend yeniden başlatıldı; health db_ok=true. Gerçek MES dosyası localhost:5173 üzerinden önizleme 4,54 sn/HTTP 200: 638 yeni, 0 unresolved, 6 free_stock, 0 pending_consumption. 5800011-02: 12 kayıt/299 adet, inputs={5800011-19:1}; standart saat değişmedi (1226,4292 toplam). 5800019-02 ve 5800067-11 tüketimleri korundu. Önizleme dışında canlı stok/plan/ithal veya kaynak aktivasyonu yapılmadı; commit/push yok. Önceki 12 pending artık geçerli değil; dosya yeniden önizlenmeli.

## 21.09.2026 — D3 Gantt görünüm düzeni (kullanıcı talebi)
Gantt /planning sekmesinden ayrılıp /gantt ve menüye taşındı. Açılış: planlanan 19 iş merkezinin haftalık plan/planlanabilir kapasite dolulukları, çoklu merkez filtresi. İkinci sekme: sipariş/mamul/WIP türüne göre tam kodlarla tek/çoklu, yalnız Gantt oluştur sonrası arama; boş seçim katalog dökmez. Her seçime tek ana satır, paralel operasyonlar gerçek tarihleri korunarak satır içi şeritlerde; ad/merkez ve hover detayları. Varsayılan ekrana sığdır, tarih ekseni, sabit kod/tarih başlığı, yakınlaştırma. Backend seçim için tarih hesaplarını yalnız seçilen satırlarda yapar ama merkezin diğer plan satırları doluluk hesabında kalır; seçim tarihlerinin orijinal Gantt ile eşitliği test edildi. Gantt hâlâ haftalık plandan yaklaşık tarih verir, kesin günlük çizelge değildir.
Doğrulama: 3 backend Gantt testi, 2 frontend seçim/yerleşim testi, TypeScript+Vite build geçti. Gerçek UI: 19 merkez dolulukları; boş arama; 6007710 plansız; 6010758 7 operasyon tek ana satır ve ekrana sığdır. Backend sağlıklı. Canlı iş verisi değiştirilmedi, commit/push yok. D3 bitiş/termin/Excel devam işleri ayrı açık.

## 21.09.2026 — YIKAMA doluluk teşhisi (salt okunur)
Plan 15.09 tarihli 6481 auto/due_date satırı; ufuk 14.09–02.11 hafta başlangıçları. 09.11 haftası kayıtlı planın dışında. 1240 açık sipariş, 18 legacy üretim (son 09.09), 0 MES. Aynı 8 haftayı mevcut simulate ile READ ONLY transaction içinde yeniden hesaplama: 6481 satır, odak merkezlerde aynı yükler. YIKAMA 702.05 saat plan, 1304.35 saat oncul_eksik ve 6.75 saat kapasite_yetersiz ile yerleşemeyen iş. Bu gerekçe toplamları sipariş sayısı değildir; mevcut algoritmanın tanısıdır. Pres1/2/3 ve tavlama 8 hafta 125 saat dolu. Kanıt JSON: backend/logs/capacity_diagnosis.json ve capacity_resimulation.json.
Ayrı kesin algoritma bulgusu: _place_quantity cycles geçişinde pred_available toplam ufuk cum_planned kullanıyor; aday haftayla sınırlandırmıyor. Bellekte 100 adet, SIVAMA hafta1=5 hafta2=95, FORMA kapasite100/hafta ve lag5: FORMA hafta1=95 çıkıyor. Gelecek hafta öncül üretimini erken kullanma hatası. Canlı GN SIVAMA→FORMA ve FORMA→ETEK KESME lag5 kuralları mevcut; gerçek planın etkilenen miktarı henüz ölçülmedi. Kullanıcıya açıklandı; bu incelemede algoritma/plan değiştirilmedi. D3 devamından önce haftalık öncül miktar sınırını düzeltme önerildi.


## 21.09.2026 — Başlangıç eşiği ve haftalık miktar akışı düzeltmesi

- Kullanıcı kararı: N adet yalnızca ardılın başlangıç eşiğidir. Eşik sonrası öncül ve ardıl kapasiteye göre birlikte devam eder; N adet kalıcı tampon olarak tutulmaz.
- operation_constraints.max_successor_qty artık eşiği çıktıdan düşmüyor. Ortak yardımcı günlük çizelgeyi de etkiler.
- planning._place_quantity ardıla yalnızca ilgili haftaya kadar biriken öncül çıktısını açar; gelecek haftanın çıktısı erken kullanılamaz. Gerçekleşmiş öncül miktarı başlangıçta eklenir, gerçekleşmiş ardıl miktarı tüketilmiş kabul edilerek tekrar kullandırılmaz.
- Regresyonlar: 5+95 haftalık akışta ardıl 5+95; yavaş ardılda stok birikimi ve sonradan tüketim; eşik bekleme; sıfır eşikte miktar korunumu; önceki gerçekleşmenin iki kez tüketilmemesi; günlük eşikte ilk partinin ve son adedin serbest bırakılması. Eski kalıcı tampon beklentili üç günlük test yeni sözleşmeye göre düzeltildi.
- Tam backend suite: 268 passed, 2 skipped (mevcut PG testleri), 137.28 s. Yeni skip yok.
- Canlı PostgreSQL SET TRANSACTION READ ONLY ile aynı 14.09–02.11 ufku tekrar simüle edildi. 6481 satır, 11217 yerleşemeyen kayıt; odak iş merkezi yükleri değişmedi. YIKAMA 702.05 saat; oncul_eksik 1304.35 saat, kapasite_yetersiz 6.75 saat.
- Kritik ayrım: bulduğumuz algoritma hatası gerçek olsa da mevcut YIKAMA dağılımının açıklaması tek başına bu değil. Tüm tanımlı rota ardışıklıklarında 40830 varsayılan finish, 3 açık item finish, yalnızca 2 group cycles eşleşmesi var. İki cycles eşleşmesi 6005510 (GN) SIVAMA→FORMA ve FORMA→ETEK KESME. Başka ürünlere 5 adet veya başka eşik uydurulmadı. Yaygın paralel akış için operasyon geçiş tanımları ayrıca netleştirilmeli.
- Haftalık plan aynı hafta içindeki kesin saat sıralamasını kanıtlamaz; günlük ayrıntılı çizelge ayrı pilot olmaya devam eder.
- Canlı kayıtlı plan yeniden oluşturulmadı; sipariş/stok/MES verisi değişmedi. Karşılaştırma logs/capacity_resimulation_fixed.json dosyasında, eski baseline korunuyor. Commit/push yapılmadı.


## 21.09.2026 — D3 / madde 6: Sipariş bitişleri göstergeleri ve Excel
- Durum kartlarına tüm sipariş pozisyonları içindeki yüzde eklendi; filtre yüzde paydasını değiştirmez. Rota yok ayrı kart, toplam ve gösterilen pozisyon sayıları görünür.
- /api/plan/orders.xlsx: mevcut iş merkezi seçimi, durum filtresi ve sıralamayla bağımsız Excel. Yetki require_user, filtre/sıralama doğrulaması mevcut. UI indirme süreci ve hata mesajı içerir.
- Bağımsız Excel ve genel plan Excel'i ortak order_schedule_sheet kullanır; pozisyon, müşteri, ürün adı ve Türkçe durum içerir. Hesaplama mevcut order_schedule kaynağıdır; yeni bitiş/gerçekleşme hesabı eklenmedi.
- Doğrulama: 11 backend export/sipariş/ciro testi geçti; TypeScript+Vite build geçti. Export testi filtre/sıra, iş merkezi parametresi, pozisyon/isim/durum kolonları, boş liste, yetki ve geçersiz parametreyi kontrol ediyor.
- D3 henüz tamamlanmadı: madde 7 WIP kod/ad, mamul araması ve miktarlı tamamlanma görünümü; son operasyon/bitiş hesabının gerçekleşme ve WIP ihtiyacıyla tutarlılığı devam ediyor. order_schedule içinde ihtiyaç halen doğrudan o.item.operations ve brüt miktardan hesaplanıyor; ortak kalan iş/WIP kaynağıyla uyumu sonraki hesap incelemesinde ele alınmalı. Bunu yalnız görsel yüzdeler eklenmesi kapatmaz.
- Canlı iş verisi/plan değiştirilmedi; commit/push yok.


## 21.09.2026 — D3 / madde 7: iş merkezi operasyon detayları
- İş merkezi bazlı siparişlerde tam mamul koduyla tek/çoklu arama; boş arama tüm plan satırlarını gösterir.
- Açılabilir detay: hafta, operasyon adı, tam yarımamul kodu/adı, plan miktarı, saat ve plan türü. Gerçekleşmiş stok girişi olarak sunulmaz.
- PlanLineOut/API/TS sözleşmesine operation_name, semi_finished_name ve eksik TS semi_finished_code alanları eklendi. Stok adları tek toplu sorguyla tam koddan okunur; kök koddan ad türetilmez.
- Aynı iş merkezindeki farklı operasyonların seq değerleri aynı olabildiğinden miktar gruplaması operation_id kullanır. Özet miktar en yüksek operasyon miktarı olarak etiketlendi; detaylar ayrı kalır.
- 11 Gantt/sipariş testi + 1 tam kod/ad regresyonu geçti; TypeScript/Vite build başarılı. Backend yerelde yeniden başlatıldı. Canlı iş verisi veya kayıtlı plan değiştirilmedi, commit/push yok.
- Bitiş/kapsam hesabı halen açık: brüt doğrudan mamul operasyon saatleri ile ortak net kalan ihtiyaç uyumsuzluğu kullanıcıya bildirildi. Ortak kalan ihtiyaç hesabına geçiş için request_user_input_async sorusu gönderildi; yanıt henüz gelmedi. Bu hesaba bağımlı miktarlı mamul tamamlanma/stoğa giriş tahmini uygulanmadı. Günlük kesin stok giriş tarihi iddiası yok.


## 21.09.2026 — D3 ortak kalan ihtiyaç hesabı (kullanıcı onayladı)
- Önceki onay bekleme notu kapandı: kullanıcı açıkça ortak kalan ihtiyaç hesabına geçişi onayladı.
- order_schedule artık required_qty_by_operation + produced_qty_map + operation_remaining + operation_run_hours kullanır. WIP ihtiyaçları, stok/rezervasyon/sevk netlemesi ve gerçekleşme aynı motor anlamıyla değerlendirilir; mevcut plan yeniden kalan ihtiyaçtan düşülmez.
- Plan kapsamı operasyon bazında miktarla sınanır; bir operasyondaki fazla plan diğerindeki eksiği gizleyemez. Forecast satırları kesin plan kapsamına katılmaz. Parti miktar/saat payı üyelerin operasyon bazındaki kalan ihtiyaçlarıyla dağıtılır.
- Kalan ihtiyaç sıfırsa covered / Üretim ihtiyacı kalmadı; sahte bitiş tarihi yazılmaz ve sevk tamamlandı denmez. Siparişler, bitiş kartı, filtre ve Excel bu durumu tanır. Üretim tarihi bilinmeden ciroya yeni gerçekleşme tarihi eklenmez.
- Stok/sevk ve WIP okumaları toplulaştırıldı. Canlı READ ONLY rapor 1240 pozisyon: 584 partial,443 unplanned,212 late,1 on_time. Hesap aynı kalarak 16.66 sn → 6.19 sn.
- Doğrulama: toplu okuma optimizasyonundan önce tam suite 271 passed/2 mevcut skip. Son optimizasyon + parti regresyonu sonrası 24 ilgili test geçti; TypeScript/Vite build ve diff kontrolü başarılı. Ayrı plan-satırı WIP tam kod/ad testi de geçti.
- Yeni regresyonlar: 100 sipariş/40 dış stok/60 plan tam kapsam; fazla öncül planı eksik son operasyonu kapatmaz; tamamen stoktan karşılanan sipariş covered; aynı partide 40 stok kredili müşteri ve 20 adet üretim kredili müşterinin kalan ihtiyaçları 60 ve80 ve doğru paylar.
- D3 kalan: son operasyon bazlı mamul tamamlanma miktarlarının haftalık sunumu, son operasyon bitişinin Gantt/tarih tahminiyle birleştirilmesi. Bu tur haftalık operasyon detayını bitmiş ürün stok girişi tahmini olarak sunmaz. İş merkezi panelinin mevcut parti ankrajı/müşteri gösterimi ayrıca korunmuş olup çok müşterili parti görünümünün iyileştirilmesi açık.
- Backend yerelde yeniden başlatıldı. Canlı sipariş, rezervasyon, stok ve plan değişmedi. Commit/push yok.


## 21.09.2026 — D3: son operasyon tamamlanma planı ve ortak tarih
- OrderScheduleOut.completion_weeks hafta, son operasyonun yaklaşık bitiş günü ve kalan ihtiyaca sınırlandırılmış mamul plan miktarı taşır. Ara operasyonların miktarları toplanmaz, forecast kesin tamamlanma miktarına katılmaz. Parti üyelerinde önceki turdaki kalan ihtiyaç payı korunur.
- Sipariş Bitiş Tarihleri detayında haftalık miktarlar; bağımsız ve genel plan Excel'inde Mamul Tamamlanma Planı sayfası. Gerçek stok girişi/rezervasyon değildir; kısmi planda önceki operasyon eksikleri nedeniyle koşullu olduğu açıkça yazılır.
- Bitiş artık mamulün kendi son rota operasyonunun planından gelir. Son operasyon yoksa veya seçili merkez kapsamı dışındaysa tarih/miktar uydurulmaz.
- orders.plan_line_window haftalık yaklaşık tarih için ortak yardımcı; Gantt da bunu çağırır. Eski sipariş bitiş döngüsünde hedef siparişten sonraki ilk satırın saatlerinin de eklenmesi kaldırıldı. Aynı haftadaki diğer siparişlerin (kapalı siparişler dahil) kapasite yerleşimi bağlamı korunur. Gantt'ın mevcut forecast bağlamı tarihte korunur; miktar hesabına forecast alınmaz.
- 18 ilgili backend testi geçti; ek kapalı-sipariş doluluk bağlamı regresyonuyla 2 tamamlanma testi tekrar geçti. TypeScript/Vite build başarılı. 40+60 tamamlanma, daha geç ara operasyonun mamul bitişini belirlememesi, forecast miktarının dışlanması, Excel ve Gantt tarih eşitliği test edildi.
- Haftalık yaklaşım saat bazlı malzeme akışını doğrulamaz. Tarih mevcut planın gösterimidir; geçersiz manuel operasyon sıralamasını bu rapor yeniden planlamaz. Veri/plan değişmedi, commit/push yok. Backend yerelde yeniden başlatıldı.
- D3 bu paketteki tarih/miktar çalışması tamam; çok müşterili parti detayında yalnız ankraj müşterinin görünmesi gibi önceki açık görünürlük notu ayrıca ele alınmalı. Genel faz kabulü tarayıcı/saha kontrolüyle tamamlanmalı.

## 21.09.2026 — D3: ortak üretim partisinde tüm müşteriler
- PlanLineOut.batch_members ve frontend sözleşmesi: partinin tüm sipariş/pozisyon/müşteri, partiye bağlı miktar ve etkin termin bilgileri. Müşteri özeti tüm üyeleri içerir; parti termini en erken üye terminidir.
- İş merkezi paneli parti ve tekil siparişi ayrı anahtarla gruplar. Ortak operasyon miktarı/saatleri çoğaltılmaz. Açılan detayda tüm üyelerin kendi plan durumu ve tahmini bitişi gösterilir; ankraj siparişin durumu bütün partiye atfedilmez. Üye miktarları rezervasyon değil, partiye katılım miktarı olarak açıklanır.
- Doğrulama: 8 ilgili backend testi geçti; üye sözleşmesi/assertion eklenince 2 kalan ihtiyaç testi tekrar geçti. TypeScript/Vite build başarılı. Backend yerelde yeniden başlatıldı.
- Tarayıcıda iş merkezi paneli, 6004002 tam kod filtresi, 5004002-29 tam kod/ad operasyon detayı (25 adet / 0,2 saat / H40), sipariş bitiş durumlarının yüklenmesi doğrulandı.
- Canlı salt okunur kontrol: üretim partisine bağlı PlanLine sayısı 0. Çok müşterili parti detayı izole regresyon verisiyle doğrulandı; canlıda çok müşterili parti görsel kabulü henüz yapılamadı. Canlıya örnek iş verisi eklenmedi.
- Bu D3 görünürlük paketi tamamlandı; genel faz saha kabulünde gerçek ortak parti örneği ayrıca görülmeli. Canlı sipariş, stok, rezervasyon ve kayıtlı plan değiştirilmedi. Commit/push yok.

## 21.09.2026 — D4 başlangıcı ve çelişkili malzeme girişi
- Günlük çizelgede expected durumu ortak material_gate_for_order ile kontrol edilir. Tarihli expected strict modda yanlışlıkla unknown sayılmaz; conditional modda da tarihten önce başlamaz. Tarihsiz expected iki modda engellenir ve doğru açıklama gösterilir.
- Parti malzeme yuvarlama açıklaması en geç tarihi gösterir. Haftalık engelleme açıklaması tarihsiz expected ile unknown strict ayrımını yapar.
- Kullanıcı Hazır + gelecekte tarih çelişkisinin engellenmesini netleştirdi. validate_material_fields oluşturma/düzenleme ve Excel için ortak kontrol: gelecek tarih varsa Bekleniyor seçilmesi istenir. Excel kontrolü satır mutasyonundan önce yapılır; hatalı satır kısmen uygulanmaz. UI tarih açıklaması güncellendi.
- İlk D4 paketi 36 ilgili test geçti; yeni CRUD/Excel regresyonuyla 14 ilgili test geçti; frontend build başarılı. Yeni test reddedilen düzenlemenin miktarı değiştirmediğini, reddedilen Excel satırının yeni sipariş oluşturmadığını doğrular.
- Canlı salt okunur taramada Hazır + gelecekte tarih çelişkili sipariş sayısı 0. Backend yeniden başlatıldı; iş verisi değiştirilmedi. Commit/push yok.
- D4 tamamlanmadı: revizyon _req malzeme politikasını taşımıyor, varsayılan conditional kullanıyor. Revizyon/karşılaştırma/termin ve kalıcı koşullu plan görünürlüğü incelemesi devam etmeli. Önceki mesajda yalnız yapılacağı söylenen giriş kontrolü bu tur gerçekten uygulandı.

## 21.09.2026 — D4 revizyon ve karşılaştırma politika aktarımı
- PlanRevision.material_policy kalıcı alanı eklendi; ensure_columns eski kayıtları conditional varsayımıyla korur. Create/Out/TS sözleşmesi ve UI aktarımları tamamlandı. _req hem önizleme hem uygulama için kaydedilen politikayı kullanır; üst seçim sonradan değiştirilse de taslak kendi politikasını korur. Liste politikayı gösterir.
- PlanCompareRequest ve revenue.compare her iki simülasyona politikayı taşır. UI karşılaştırma, uygulama, ön kontrol ve kalite değerlendirme isteklerinde üst seçimi gönderir. Ufuk/merkez/politika değişince eski karşılaştırma sonucu gösterilmez ve uygulanamaz.
- 10 revizyon/snapshot/ciro testi + 8 D4 testi geçti. Katı ve koşullu mod için API üzerinden oluşturma-hesaplama-onay ile karşılaştırma regresyonları eklendi. Frontend build ve diff check başarılı.
- Backend yerelde yeniden başlatıldı. Canlı kayıtlı plan yeniden oluşturulmadı; commit/push yok. Bu tur tarayıcı görsel kabul yapılmadı.
- D4 kalan: termin tahmini ve kalıcı koşullu plan uyarısının Gantt/rapor/Excel kapsamı. Bu paket Faz 4 bütününü kapatmıyor.

## 21.09.2026 — Otomatik planlama zaman aşımı
- Kullanıcı revizyonsuz plan ön kontrollerden sonra zaman aşımı bildirdi. frontend/api.ts tüm isteklerde 20 saniye AbortController; backend.out.log aynı POST /api/plan/auto için 200 OK içeriyor. Tarayıcı keserken sunucu hesaplayıp kaydetmeye devam ediyor; hata planın başarısız olduğunu kanıtlamıyor.
- Yeni /plan/auto/jobs POST ve kullanıcıya özel GET: tek aktif otomatik iş, aynı kullanıcı/payload için aktif işe yeniden bağlanma, ayrı DB oturumu, mevcut run_auto_plan ön kontrol/onay yolu aynen kullanılır. Sonuç/hata takip edilir. Yerel tek worker mimarisinde bellek içi takip; sunucu restartında takip kaybolur ve 404 açıkça planı kontrol etmeyi ister.
- Ana Planning butonu artık uzun POST beklemez: kısa başlatma ve 1 saniye durum sorgusu, faz yazısı, sessionStorage ile sayfa yenilenince takip sürer. Ağ hatasında mevcut kimlik saklanır; yeni plan yerine mevcut takip sürdürülür. Eski /plan/auto endpoint diğer istemciler için korunur; bu paketin kapsamı ana revizyonsuz butondur.
- 4 preflight + 10 job/kapasite testi geçti; temizlik sonrası 3 job testi tekrar geçti. Frontend build başarılı. Testlerde aktif iş tekrarının ikinci iş yaratmaması, başka kullanıcı erişimi, onay tokenının aktarımı, başarılı/hatalı sonuç test edildi. Canlı plan yeniden çalıştırılmadı.
- D4 termin/kalıcı malzeme görünürlüğü işleri bu acil düzeltme için araya alındı, henüz tamam değil. Commit/push yok.

## 21.09.2026 — Uyarı nedenleri ve sıfır süre düzeltmesi
- Kullanıcı varsayılan finish korunmasını ve sıfır operasyon süresinin geçersiz olduğunu netleştirdi. Canlı tüm rota geçişleri RuleLookup ile salt okunur tarandı: sadece6005510 üzerinde5 varsayılan-dışı eşleşme;2 group/cycles (GN,5),3 item/finish. GN grup kuralları gelecekte eşleşen başka ürünlere uygulanabilir; hiçbir kural değiştirilmedi.
- assembly cap=0 ilk mamul operasyonu yarimamul_eksik; önceki kapasite_yetersiz yanlışlığı düzeltildi. UI nedenleri anlaşılır Türkçe açıklıyor; genel başlık Yerleştirilemeyen ve nedenleri inceleyin.
- Sıfır/negatif çevrim süreli kalan operasyon yerleştirilmez, operasyon_suresi_eksik. Tamamı sıfır saatli adayların sessiz düşmesi kaldırıldı. Süre uydurulmadı.
- Küçük unplanned saatler8 ondalıkla korunur, UI0,01 saat altını saniye gösterir. Veri eksiğinde Süre hesaplanamadı yazar.
- Tüm rota taraması51 sıfır/negatif süreli operasyon: outputs/sifir-sureli-operasyonlar.md. Önceki87 uyarıdaki17 kayıt yalnız7 farklı operasyondu; tam master listesi daha geniş.
- 18 geçiş/kapasite testi; aday düzeltmesinden sonra12 teşhis/ciro/netleme testi; son3 teşhis testi başarılı. Frontend build geçti. Backend yeniden başlatıldı; mevcut plan yeniden çalıştırılmadı. Commit/push yok.

## 22.09.2026 — D4 devamı: yeni iş terminlemede malzeme
- LeadTimeRequest/Out ve ForecastFromLeadTimeIn malzeme durumu, hazır olma tarihi ve conditional/strict politikası taşır. Ortak malzeme giriş doğrulaması ve material_gate kullanılır. Bilinmeyen+strict termin/forecast engellenir; conditional açık uyarıyla döner. Expected tarihten önce başlamaz; termin hesabı gün bazında verilen tarihi kullanır.
- UI ayrı malzeme alanları ve politika; giriş değişince eski sonuç temizlenir. Hesap sırasında alanlar kilitlenir. Forecast kaydında malzeme durumu/tarihi Order üzerinde, koşullu açıklama note içinde korunur. Liste bu bilgiyi gösterir. Forecast kaydı gelen adımların malzeme tarihinden önce olmasını reddeder.
- 23 malzeme/termin/kapasite testi geçti. TypeScript/Vite build ve diff check başarılı. Canlı iş verisi ve mevcut plan değiştirilmedi; yeni şema gerekmedi. Commit/push yok.
- D4 kalan: ana plan satırlarında kalıcı koşullu durum ve Gantt/genel rapor/Excel görünürlüğü. Faz bütünü henüz tamam değil. Önceki tam-suite ciro test etkileşimi bu pakette kapatılmadı.

## 22.09.2026 — D4 rapor görünürlüğü
- Sipariş bitiş raporu kayıtlı plan satırlarından koşullu ve geçmiş koşulu bilinmeyen satır sayılarını taşır; bugünkü sipariş malzeme durumu geçmişe uygulanmaz. Ortak parti ve simülasyon satırları aynı rapor yolundan geçer.
- UI tarih durumunu değiştirmeden malzeme açıklamasını yanında gösterir. Sipariş bitiş Excel ve mamul tamamlanma açıklamaları aynı bilgiyi taşır. Genel plan Excel'inde Plan Koşulları sayfası eklendi.
- Ciro özetinde koşullu/bilinmeyen malzeme koşulu olan sipariş pozisyon sayıları gösterilir; kümeler örtüşebilir. Ciro hesabı değiştirilmedi.
- 18 ilgili test geçti; son Excel değişikliğinden sonra 3 test tekrar geçti. Frontend build başarılı. Backend yeniden başlatıldı; mevcut plan yeniden oluşturulmadı. Commit/push yok.
- D4 için listelenen malzeme politikası ve rapor görünürlüğü uygulamaları tamamlandı. Genel kabul ve önceki tam-suite ciro test etkileşimi ayrı açık takip konusu; bu tur tam suite çalıştırılmadı.

## 22.09.2026 — D4 genel kabul kontrolü
- Kullanıcı talebiyle bilinen test_modes_and_compare ciro testi kapsam dışı bırakıldı; test koduna skip/xfail eklenmedi.
- Komut: python -m pytest -p no:cacheprovider tests/ -q -k "not test_modes_and_compare". Sonuç: 330 passed, 2 skipped, 1 deselected; 95.37 saniye. Mevcut PostgreSQL ortamı gerektiren atlamalar devam ediyor.
- Frontend npm test: 9 test başarılı. Son frontend derlemesi önceki uygulama adımında başarılı; bu kabul turunda ürün kodu değiştirilmedi.
- Canlı browser kontrolü: /planning sipariş bitiş raporu 1240 pozisyonla açıldı, eski planın malzeme koşulu kaydedilmemiş açıklaması göründü. /gantt iş merkezi dolulukları ve kaydedilmemiş malzeme koşulu sayıları yüklendi.
- Backend health PostgreSQL db_ok true. Kapalı frontend arka planda başlatıldı. Canlı plan yeniden oluşturulmadı, sipariş/stok değiştirilmedi ve veri güncelliği onaylanmadı.
- D4 genel kabulü bu kapsam ve test istisnalarıyla tamamlandı. Ciro testi ayrı açık kayıt olarak bırakıldı. Sonraki planlı faz D5 uzun işlem yönetimi.

## 22.09.2026 — D5 ilk paket: otomatik plan başlatma yanıtı kaybı
- Tarayıcı işlem kimliğini POST öncesinde sessionStorage'a kaydeder; başlatma yanıtı alınamazsa GET ile aynı işlemi sorgular. Başarılı/başarısız tamamlanmış isteğin aynı kimlikle tekrarı TTL içinde aynı sonucu döndürür. Kimlik farklı kullanıcı/payload için kullanılamaz.
- Durum yanıtı geçen saniyeyi taşır. Poll bağlantı hatasında takip kimliği korunur. 404 durumunda eski genel mesaj yerine yeniden başlama/ulaşmayan istek belirsizliği açıkça anlatılır; otomatik POST tekrarı yapılmaz.
- 20 backend testi ve 12 frontend testi geçti; frontend build başarılı. Backend güncel kodla yeniden başlatıldı. Canlı planlama tetiklenmedi, commit/push yok.
- Sınır: job store hâlâ tek worker belleğinde, tamamlanan işler 30 dk tutulur; sunucu restartından sonra kalıcı takip yok. D5 tamamlanmadı.
- Sonraki D5: auto_plan günlük ayrıntı yolunda write_simulation commit'i günlük çizelge üretiminden önce çalışıyor; günlük hesap hatasında haftalık planın kaydedilmiş kalması ihtimali giderilmeli. MES preview hâlâ senkron; ölçüm ve takip kapsamı incelenecek. Merge impact mevcut takip servisiyle birlikte toparlanma/işlem bütünlüğü kabulü yapılacak.

## 22.09.2026 — D5 devamı: tek işlemde plan kaydı ve MES önizleme takibi
- auto_plan haftalık yazımı commit=False ile yapar; günlük çizelge de başarılı olduktan sonra tek commit. commit=True hata yolunda rollback. commit=False çağıranın işlem sahipliği korunur. Gerçek SQLite replacement testi eski planın günlük hata sonrası korunduğunu, başarıda iki kısmın tek commit aldığını ve dış rollback'i doğrular.
- MES /preview/jobs POST ve GET eklendi. Tek worker, kullanıcı başına tek aktif dosya; aynı içerik aktif işi döndürür, toplam 4 bekleyen/çalışan sınırı. Sonuçlar 15 dakika/8 tamamlanmış iş sınırıyla temizlenir. PostgreSQL REPEATABLE READ + READ ONLY; önizleme üretim yazmaz. Mevcut /preview API korunur.
- Progress ekranı dosyayı yeni kuyruğa gönderir, süre/durum gösterir. Kopmada aynı dosyanın seçilmesiyle aktif işe dönülebilir; tamamlanmış iş varsa yeni salt okunur önizleme hesaplanır. Dosya onayı ve token kontrolü mevcut /import yolunda kalır.
- İlk plan paketi 36 passed; MES/atomic paketi 47 passed; frontend 13 passed ve build başarılı. Backend yeniden başlatıldı. Canlı üretim aktarılmadı veya plan yeniden hesaplanmadı; commit/push yok.
- D5 tamamlanmadı: MES onaylı import hâlâ senkron; uzun işlem ölçümü, import sonucunun belirsiz kalmasına karşı takip, merge impact toparlanma ve genel kabul sırada. Job store bellekte olduğundan restart sonrası iş kurtarma yok; kullanıcıya yeniden önizleme/kayıtlı plan kontrolü mesajı gösterilir.

## 22.09.2026 — D5 devamı: onaylı MES aktarım takibi
- /api/mes/import/jobs POST + GET; tek etkin yazıcı, işlem kimliği ve kullanıcı/dosya hash/token eşleşmesi. Aynı kimliğin tekrar onayı 30 dakika saklanan sonucu döndürür. Mevcut senkron import yolu ve PostgreSQL işlem kilidi korunur.
- Worker apply_import token doğrulamasını aynen kullanır, tek commit; hata halinde rollback. Tamamlanan iş yalnız counts saklar. Veri/BOM değişirse aktarım reddedilir.
- Tarayıcı işlem kimliğini gönderim öncesinde kaydeder; yanıt kaybında GET ile kontrol eder. Sayfa yenilemede aynı aktarımı takip eder, yeniden dosya göndermez. 404/restart durumunda otomatik aktarım yapılmaz; kayıtları kontrol etme mesajı gösterilir.
- 48 MES backend testi, 16 frontend testi ve build başarılı. Gerçek kayıt rollback ve tamamlanmış isteğin mükerrer yazmaması test edildi. Canlı MES dosyası aktarılmadı. Commit/push yok.
- D5 kalan: merge impact toparlanma, gerçek veriyle salt okunur süre ölçümü ve genel kabul. Bellekte tutulan iş sonuçları restart sonrası kalıcı değildir; bu sınırlama kullanıcıya açıklandı.

## 22.09.2026 — D5 son paket: birleştirme toparlanma ve ölçüm
- Merge impact istemci kimliği POST öncesinde saklanır. Aynı seçimle tekrar deneme kaydedilmiş işe döner; farklı payload eski sonucu kullanmaz. Sayfadan çıkınca poll durur; aynı seçimle devam edilebilir. Sunucu kimlik/kullanıcı/payload eşleşmesini kontrol eder, tamamlanan sonucu TTL boyunca döndürür ve geçen süreyi bildirir.
- Salt okunur PostgreSQL REPEATABLE READ + READ ONLY ölçümü: 638 satırlık MES önizleme 4.114 sn; 8 haftalık simulate 69.988 sn (7108 taslak satır, 8915 yerleşmeyen); bir uygun sipariş çifti merge impact 26.601 sn. Dosya: outputs/d5-timings.json; tekrar aracı backend/scripts/check_d5_timings.py. Bunlar testlerle aynı makinede gözlenen sürelerdir, izole benchmark değildir. Canlı plan/import yazılmadı.
- Genel pakette Excel yedek testinin ilk rota satırının kendi ürünü olduğu varsayımı yeni atomic test verisiyle bozuldu. Test doğru Stok Kodu satırını seçer hale getirildi; doğrulama kaldırılmadı. Bilinen ciro testi kullanıcı talebiyle kapsam dışı kalır.
- Başlatma betiğinin sabit 4 saniye sonrası yanlış hata bildirimi, süreç yaşamı + db_ok durumuna göre en fazla 30 saniyelik hazır olma kontrolüne çevrildi.
- Frontend 18 test ve build başarılı. Job store kalıcılığı tek yerel worker belleği/TTL ile sınırlı; restart sonrası otomatik yazma tekrarı yok, kullanıcı kayıt/önizleme kontrolüne yönlendirilir.
- D5 genel kabul sonucu: 353 passed, 2 mevcut PostgreSQL ortam skipped, 1 kullanıcı isteğiyle ciro deselected (195.39 sn). Frontend 18 passed, build başarılı. Başlatma doğrulaması 127.0.0.1 ve istek başına 5 saniye ile başarıyla test edildi; health db_ok true.
- D5'in otomatik plan/MES önizleme-onaylı aktarım/birleştirme analizi takip ve hata toparlanma kapsamı bu sınırlar içinde tamamlandı. Sunucu restartından sonra kalıcı job kurtarma kapsam dışı sınırlama olarak korunur. Canlı iş verisi değişmedi; commit/push yapılmadı.

## 22.09.2026 — D5 kalıcı işlem kaydı: yeniden başlatma eksiği kapatıldı
- background_jobs tablosu eklendi; otomatik plan, MES import, MES preview ve merge impact ortak durable_jobs servisine taşındı. Kimlik, kullanıcı, istek özeti, durum, sonuç ve zamanlar PostgreSQL/SQLite üzerinde kalıcıdır; önceki bellek/TTL kaybı yeni işler için kaldırıldı. Ham Excel dosyaları saklanmaz.
- Plan/MES yazma işlemi ile done sonucu aynı transaction içinde commit edilir. Plan worker ortak execute_auto_plan(preflight dahil) yolunu commit=False ile kullanır. Commit yanıtı kaybolursa hata işleyicisi tamamlanmış sonucu bozamaz. Aynı kimliğin yeniden gönderimi kullanıcı/tür/fingerprint eşleşmesiyle eski sonucu döndürür, tekrar yazmaz.
- Tek worker yerel startup, önceki süreçten queued/running kalan işleri failed + açık yarım kalma açıklamasına çevirir; otomatik yeniden yazma yapılmaz. Daha önce commit edilmiş işler done kalır. salt okunur analiz sonucu ayrı kısa işlemde kalıcı kaydedilir. Mevcut plan/MES iş verisi taşınmadı veya sıfırlanmadı.
- 19 iş servisi/kalıcılık testi başarılı; ek gerçek subprocess os._exit sınır testleriyle durable paketi 8 passed. Tamamlanmış verinin ve sonucun birlikte kalması, commit öncesi gerçek süreç ölümünde rollback, yarım işin startup'ta açıklanması, tekrar yürütmenin engellenmesi test edildi.
- Yerel PostgreSQL kabulü: 638 satırlık gerçek MES dosyası sadece önizlendi. Backend gerçekten restart edildi; aynı token/638 satırlık sonuç korundu, queued metadata denemesi açık failed oldu. Kanıt outputs/durable-restart-check.json; araç backend/scripts/check_durable_restart.py. Gerçek üretim importu veya yeniden planlama yapılmadı.
- Frontend 18 test ve build başarılı. Geniş backend koşusu 379 passed, 1 failed, 2 skipped, 1 ciro deselected: tek hata önceden var olan Excel BOM testinin ilk satır varsayımıydı. Stok Kodu ile doğru satır seçimine düzeltildi; ardından e2e + backup paketi 24 passed, 1 mevcut PG skipped. Son test düzeltmesinden sonra tam paket tekrar edilmedi. Yeni skip/xfail yok.
- Backend güncel sürümle yeniden başlatıldı; health PostgreSQL db_ok true. Commit/push yok. Önceki D5 kapanışındaki kalıcı takip sınırı bu değişiklikle kapatıldı; eski sürümde bellekte kaybolmuş sonuçlar geriye dönük üretilemez.

## 22.09.2026 — D5 nihai regresyon kabulü ve saha doğrulaması başlangıcı
- Kalıcı işlem kaydı sonrasındaki tam backend koşusu: 380 passed, 2 mevcut PG skipped, 1 kullanıcı kararıyla ciro deselected; 313.92 sn. Komut: python -m pytest -p no:cacheprovider tests/ -q -k "not test_modes_and_compare". Yeni skip/xfail yok. Frontend 18 passed; npm run build başarılı (sandbox esbuild spawn EPERM sonrası izinli tekrar). D5 teknik kabulü bu kapsamla kapandı.
- Salt okunur PostgreSQL REPEATABLE READ incelemesi: 1240 sipariş, 10217 plan satırı, 18 legacy üretim, 5410 stok girişi, 0 rezervasyon, 0 MES kaydı. Aktif kaynak legacy. Bitiş raporu 1240 pozisyonu döndürüyor; negatif ihtiyaç/plan saati ve başlangıçtan önce bitiş yok. Bu kontroller bütün planın doğru olduğunu tek başına kanıtlamaz.
- 529 parti plan satırı / 105 planlı parti / 86 çok müşterili parti. plan_lines servis çıktısında üyeler DB bağlantılarıyla eşleşiyor, müşteri özetinde eksik üye yok, 10217 satırın tamamı dönüyor. Bu gerçek veri/API kabulüdür; bu tur tarayıcı görsel kabulü yapılmadı.
- Kullanıcı saha doğrulamasında önceki Uretim_Kayitlari_Raporu_17 Eylül 2026 14_40.xlsx dosyasını seçti. Güncel BOM ile salt okunur önizleme: 638 yeni, 0 unresolved, 0 pending_consumption, 6 free_stock; 58777 net adet (farklı aşamaların toplamıdır, bitmiş ürün adedi değildir), 1181.63487 standart saat.
- Varsayımsal MES defteri 382 kod içeriyor; üretim-tüketim=bakiye aritmetiği tutarlı; tüketim hareketlerinden ikinci kez işçilik yazılmıyor. 81 kodda eksik bakiye: 60 kod için dosyada pozitif üretim hiç yok, 21 kodda üretim tüketimi karşılamıyor. Açılış sıfır kararı korunuyor; geçmiş stok/eksik beyan olasılığı vardır, BOM doğruluğu bu sonuçtan kanıtlanamaz. Bu uyarılar kapatılmadı.
- Tekrar aracı backend/scripts/check_field_acceptance.py; kanıt outputs/field-acceptance-readiness.json. Henüz canlı MES importu, kaynak geçişi veya plan yeniden oluşturma yapılmadı. Gerçek MES sonrası kalan kapasite ve sipariş bitiş zincirinin saha kabulü açık; izole uçtan uca testlerin geçmesi bunun yerine sayılmadı. Commit/push yok.

## 22.09.2026 — Kullanıcı onayıyla deneme MES geçişi ve gerçek veri kontrolü
- Kullanıcı programı henüz aktif kullanmadığını belirterek deneme aktarımı/kaynak geçişini onayladı. Önce backend/backup/kapasite_20260922_131438.dump alındı (4204430 byte); pg_restore --list ile arşiv okunabildi. Restore provası yapılmadı. Önceki kaynak legacy idi.
- Önceki onaylı 17 Eylül 14_40 MES Excel dosyası güncel önizleme token'ıyla kalıcı mes_import_jobs üzerinden aktarıldı: 638 yeni, 0 unresolved, 6 free_stock, 0 pending_consumption. Tekrar önizleme 638 unchanged / 0 yeni / 0 güncelleme; ikinci kez import yapılmadı.
- backend/.env PRODUCTION_SOURCE=mes yapıldı, backend yeniden başlatıldı. Canlı HTTP health db_ok=true; source-status mes/has_mes_records=true. Aktarım işi restart sonrası done olarak korundu.
- Gerçek MES defteri: 382 kod / 998 hareket / 81 negatif bakiye. Sıfır açılış korunuyor; eksik bakiyeler doldurulmadı. Bunlar veri sınırıdır; stokların saha gerçekliği doğrulanmış sayılmaz.
- Kayıtlı plan değiştirilmeden kaynak öncesi/sonrası karşılaştırma: kalan sipariş işçiliği 25418.53 -> 25294.82 saat (123.71 azalma), 28 pozisyonda değişim. Toplam standart MES çıktısı 1181.63487 saat; mevcut auto/manual plana eşleşen 646.92904 saat, plan 14283.126 saat, kalan 13636.19696 saat. Kalan sipariş ihtiyacı ile mevcut plan gerçekleşmesi farklı ölçümlerdir; eşit olmaları beklenmez. Eski üretim MES'e eklenmedi.
- Durumlar önce 116 geç /1084 kısmi/37 plansız/3 zamanında; sonra 116/1085/37/2. Kaynaklar aynı tarih aralığını temsil etmediğinden değişim salt üretim artışı olarak yorumlanmamalı.
- Yeni açık bulgu: legacy öncesi raporda W00000000386189 poz4 /6005510 için planned_end=None iken on_time verilmiş. orders.order_schedule son else dalı tarihi olmayan tam kapsamı zamanında sayabiliyor. MES sonrası bu satır partial; güncel MES raporunda on_time olup tarihi olmayan kayıt yok. Kod sorunu bu tur düzeltilmedi; rapor durumu sözleşmesiyle birlikte sonraki düzeltme. Genel saha kabulü bu nedenle tamamen kapatılmadı.
- Kanıtlar outputs/mes-cutover-20260922 altında before/after, changed-orders, import-result, live-api-check, final-counts. Araç scripts/accept_mes_cutover.py --apply açık yazma seçeneğiyle çalıştı. Kayıtlı plan yeniden oluşturulmadı; BOM/sipariş tanımları değiştirilmedi. Commit/push yok.

## 22.09.2026 — Son operasyon bitişi olmayan siparişte yanlış zamanında etiketi
- Kullanıcı onayıyla orders.order_schedule sınıflaması düzeltildi: seçili kapsam miktarca tam fakat planned_end yoksa finish_unknown / Bitiş tarihi belirsiz. Tarih yokken on_time üretilmez. covered, no_ops, unplanned ve partial öncelikleri korunur; tarih veya tamamlanma miktarı uydurulmaz.
- Sipariş Yönetimi ve Sipariş Bitiş Tarihleri etiket/filtreleri, planlanan sipariş filtresi, TS sözleşmesi ve Excel durum filtresi/etiketi birlikte güncellendi. Ortak PlanStatusBadge üzerinden karşılaştırma/iş merkezi detaylarında da aynı etiket kullanılır. Ciro/başarı sayacı on_time olarak saymaz.
- Regresyon: yalnız ilk merkez seçiliyken %100 kapsam + bilinmeyen bitiş; tüm rotada eksik son operasyonun partial kalması; Excel filtre/etiketi; son operasyon eklendiğinde gerçek tarihle on_time. İlgili tamamlanma/kalan ihtiyaç/Excel/KPI/revizyon/ciro testleri 15 passed, 1 bilinen ciro testi deselected. Test sürecinde PRODUCTION_SOURCE=legacy açıkça seçildi; canlı ayar mes kaldı. Frontend TypeScript/Vite build başarılı.
- Backend güncel kodla yeniden başlatıldı. Bu değişiklik mevcut plan/üretim/stok verisini değiştirmez. Önceki saha kabulü açık hata kaydı bu düzeltmeyle kapatıldı; genel saha/BOM doğruluğu iddiası değildir. Commit/push yok.
