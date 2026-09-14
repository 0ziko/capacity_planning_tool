# MES üretim raporu — entegrasyon incelemesi

Kaynak: `Uretim_Kayitlari_Raporu_14 Eylül 2026 17_38.xlsx`. İnceleme tarihi: 14.09.2026. Karşılaştırma, aynı tarihteki yerel rota ve makine tanımlarına karşı salt okunur yapıldı. Dosya üretim veritabanına yüklenmedi.

## Sonuç

**Kapsam revizyonu — 14.09.2026, kullanıcı yönlendirmesi:** Üretim müşteriden bağımsız yürür. Müşteri eşleştirmesi bitmiş ürün sonrası Stok & Rezervasyon sürecine aittir. MES entegrasyonunun ana çıktısı `/progress` üzerinde ürün, yarımamül, operasyon ve iş merkezi bazında günlük/haftalık üretim analizidir. Aşağıdaki tasarım ve uygulama sırası bu kararı esas alır; mevcut ekrana henüz uygulanmış özellikler değildir.

Bu rapordan operasyon bazında üretim, sağlam/hurda miktarları ve gerçekleşen süreler çıkarılabilir. Ancak dosya mevcut uygulamaya **olduğu gibi, güvenilir biçimde import edilemiyor**. Ayrı bir MES adaptörü, kalıcı kaynak kayıt kimlikleri, kontrollü rota eşleştirmesi ve miktar politikası gerekir. Mevcut genel üretim importuna yalnızca başlık dönüştürerek bağlamak yeterli değildir.

Günlük üretim akışı, başlangıç yarımamül ve bitmiş stokları, doğru rota/reçete miktarları, güncel siparişler ve iş gücü takvimiyle birlikte kalan işi güncelleyebilir. Tek günlük rapor, önceki günlerden devreden üretimi veya depoda fiziksel olarak kullanılabilir miktarı tek başına kanıtlamaz. Güncel kalan iş ile mevcut planın tekrar çizelgelenmesi ayrı adımlardır; veri importu tek başına planı otomatik yeniden oluşturmaz.

## Dosyanın somut bulguları

- 139 veri satırı ve 139 farklı Üretim Detay Id var; Üretim Id ise 125 farklı değer içeriyor. İlk örnekte detay kimliği tekil; kimliğin farklı gün/tesislerde de tekil olduğu MES sözleşmesinden doğrulanmalı.
- Tarih sütununda 14.09.2026 için 133, 13.09.2026 için 5, 04.08.2026 için 1 satır var. Dosya adı üretim tarihi yerine kullanılamaz.
- 69 satırda Bitmiş Ürün boş; 69 satırda Sipariş No boş. Sipariş/müşteri bilgisinin boş olması üretim kabulünü veya raporlamayı engellemez. Bitmiş ürün belirsizse tanınan yarımamül/operasyon çıktısı kendi stok havuzunda izlenir; doğrulanmış reçete ilişkisi olmadan bir bitmiş ürüne atanmaz.
- Tarih + malzeme kodu + sipariş anahtarında 16 grup birden fazla detay içeriyor.
- Malzeme kodu ve varsa Bitmiş Ürün ipucuyla mevcut yarımamül rota indeksinde 65 satır tek aday, 60 satır birden fazla aday, 14 satır sıfır aday verdi. Bu, bu iki alanla yapılan aday taramasıdır; tam MES eşleştirme başarısı veya 65 satırın koşulsuz import edilebilirliği anlamına gelmez. Makine, operasyon ve reçete kontrolleri ayrıca gerekir. Sipariş/poz eşleşmesi bu üretim akışının zorunlu koşulu değildir.
- MES İş Merkezi Kodu, çoğunlukla uygulamadaki **makine kodudur**: HP-60 gibi. 139 satırın 138'inin kodu makine tablosunda bulunuyor. Makinenin tanınması, o rotada çalışmaya yetkili olduğunun kanıtı değildir; ana/alternatif istasyon kontrolü gerekir.
- İş emri durumları: 68 Bekliyor, 68 Üretimde, 3 Tamamlandı. Bekliyor durumunda da üretim miktarı olan kayıtlar var; durum alanı üretim miktarını geçersiz saymak için kullanılmamalı.
- Dosyanın stil tanımı mevcut openpyxl okuyucusunda `expected <class 'openpyxl.styles.fills.Fill'>` hatası veriyor. İnceleme için ham XLSX XML verisi okundu; kaynak dosya değiştirilmedi. MES adaptörü bu üretici biçimini desteklemeli veya üreticinin geçerli XLSX çıktısı sağlaması gerekir.

## Doğru ve yanlış hesap örnekleri

`5000983-21` yarımamülü, `6000983` bitmiş ürünü, `W00000000385014` siparişi için aynı gün dört detay var: 0, 0, 173 ve 44. Detay üretim toplamı **217**. İş emri bazında toplam üretilen miktar her satırda 217 olduğu için bu sütunun toplamı 868 olur ve yanlıştır. Mevcut gün/yarımamül/sipariş üzerine yazma yaklaşımıyla son satırın 44 adedi kalabilir; bu da yanlıştır. MES operasyon kodu 21, mevcut rotadaki sıra ise 10: MES operasyon kodu doğrudan uygulamanın operasyon sırası değildir.

Bu 217 adet delik delme operasyonunun üretimidir; aynı sayıda bitmiş evyenin bütün operasyonlarının tamamlandığını göstermez. Yeni raporda üretim, müşteri bilgisine bakılmadan ürün/operasyon planına karşı değerlendirilir. Bitmiş ürün tamamlanması için önceki üretimler, başlangıç stokları ve her gerekli operasyon dikkate alınır.

`5011261-12`, detay 5294: Üretilen Miktar 287, Net Üretilen Miktar 286, Sağlam Adet 286, Hurda 1. Kapasiteden düşülecek tamamlanma miktarı için brüt üretim ile sağlam üretim ayrılmalıdır.

`5005512-03`, detay 5221: Üretilen Miktar 0, Net Üretilen Miktar 0, Sağlam Adet -2, Hurda 2. Bu satır sessizce sıfıra yuvarlanmamalı veya negatif bitmiş stok yaratmamalı. Geriye dönük kalite düzeltmesi mi, başka üretim detayına bağlı hurda mı olduğu doğrulanmalıdır.

Tüm dosyada Üretilen Miktar toplamı 10.363, Net Üretilen Miktar 10.362, Sağlam Adet 10.360, Hurda 3. Bunlar **farklı ürün ve operasyonların satır toplamlarıdır**, bitmiş ürün adedi değildir. Sağlam toplamı negatif düzeltme satırını içerir; doğrulanmadan stok kabulü için kullanılmamalıdır. İş emri toplam sütununun satır toplamı 40.629'dur ve tekrarlar nedeniyle günlük çıktı olarak kullanılamaz.

## Mevcut programın karşılayabildiği ve karşılayamadığı noktalar

Genel üretim importu tarih, yarımamül, bitmiş stok ipucu, iş merkezi, operasyon sırası, sipariş ve miktarı alabiliyor. Üretim kayıtlarında ayrıca iyi adet, hurda, rework ve süre temeli alanları bulunuyor. Ancak MES detay kimliği ve iş emri kimliği için ayrı kaynak kayıt modeli bulunmuyor. Dolayısıyla aynı detayın tekrar yüklenmesini, gün içi artışını, düzeltmesini ve iptalini kaynak kimliğinden takip edemiyor.

Yarımamül birden çok rotada kullanılıyorsa mevcut akış plan/termin önceliğine göre seçim veya dağıtım yapabiliyor. Bu bir planlama varsayımıdır; MES'te üretimin gerçekte hangi bitmiş ürüne ait olduğuna dair izlenebilirlik değildir. Yeni adaptör belirsiz eşleşmeyi onaysız biçimde böyle dağıtmamalıdır.

`remaining_work.produced_qty_map` kalan işi hesaplarken `ProductionActual.quantity` kullanıyor; stok üretim zinciri hesabı da miktara dayanıyor. Bazı KPI hesapları `good_qty` alanını kullanıyor. Brüt üretimi quantity'ye, sağlamı yalnızca good_qty'ye koymak mevcut durumda tamamlanmayı ve stok hesabını tutarsız yapabilir. Sağlam üretim ve düzeltme politikası bütün tüketicilerde birlikte düzenlenmeli.

Sipariş no + bitmiş ürün birden fazla açık poza uyuyorsa mevcut kalan iş hesabı uyarı verip otomatik atamıyor. Bu mevcut yaklaşımın müşteri bağımsız üretim raporunun temeli olarak kullanılmaması gerekir: yeni hesap ürün/operasyon havuzundan çalışmalı, sipariş alanını sadece isteğe bağlı kaynak bilgisi olarak saklamalı. Lot No, sipariş pozuyla eşit varsayılmamalı; bu ilişkinin çözülmesi `/progress` için ön koşul değildir.

## `/progress` hedef tasarımı

### Gün ve hafta içinde aşamalı takip

Seçilen üretim tarihi hangi haftadaysa o haftanın planı esas alınır. Üst bölümde hafta, kontrol günü, iş merkezi, makine, stok/yarımamül ve operasyon filtreleri bulunur; müşteri filtresi ana akışa konmaz. Aynı ürün/operasyonun farklı müşteri siparişlerinden doğan plan satırları raporda birleştirilir. Farklı operasyon miktarları toplanıp bitmiş ürün adedi gibi gösterilmez.

Üst özet kartları: planlı işçilik saati, planla eşleşen üretimin standart işçilik karşılığı, plan gerçekleşme oranı, kalan tahmini işçilik, plan dışı üretimin standart saat karşılığı ve veri güncelliği. Farklı ürünleri karşılaştıran genel toplamlar ortak saat biriminde; adetler ürün/operasyon düzeyinde sunulur.

Günlük görünümde haftanın günleri boyunca planlanan ve gerçekleşen üretim, birikimli ilerleme eğrisi ve iş merkezleri için renkli durum matrisi gösterilir. Günlük detaylı çizelge varsa gün hedefleri oradan alınır. Yalnızca haftalık plan varsa günlük hedef uydurulmaz: haftalık hedef çizgisi ve gün gün gerçekleşen gösterilir; çalışma takvimine göre hesaplanan referans ilerleme varsa açıkça “referans” diye etiketlenir. Tatiller, vardiyalar ve seçili kontrol gününe kadar geçen çalışma süresi hesaba katılır.

Ürün satırı açıldığında yarımamül/operasyon zinciri görünür: haftalık hedef, o günkü sağlam üretim, hafta içinde birikimli üretim, kalan miktar, fazla üretim, tahmini kalan işçilik ve veri/eşleştirme uyarısı. Kullanıcı bir sapmadan ilgili MES detaylarına inebilmelidir. Kaynağı eksik veya belirsiz veriler sıfır gerçekleşme gibi sunulmaz.

### Hesap tanımları

- **Kalan haftalık miktar:** `max(haftalık operasyon hedefi − bu hedefe tahsis edilen doğrulanmış sağlam üretim, 0)`. Fazla üretim ayrı gösterilir. Hafta öncesi gerçekleşen üretim, netleştirilmiş haftalık hedefe ikinci kez mahsup edilmez.
- **Kalan tahmini işçilik:** Kalan operasyon miktarlarının geçerli standart insan işçiliğiyle çarpımı ve yalnızca henüz gerekli olan kurulum işçiliği. Standart kişi-saat bilgisi yoksa “hesaplanamıyor” gösterilir; makine çevriminden otomatik insan saati türetilmez. Bu tahmin takvimde kesin bitiş tarihi değildir.
- **Plan gerçekleşme oranı:** Plan hedefleriyle eşleştirilen sağlam çıktının, planın standartlarına göre işçilik karşılığı / aynı kapsamdaki planlı işçilik saati. Fazla ve plan dışı üretim başka operasyonların eksiklerini örterek oranı yükseltmez; ayrı gösterilir.
- **Planlanan kapasite kullanımı:** Planlı işçilik saati / kullanılabilir işçilik kapasitesi.
- **Gerçekleşen kapasite kullanımı:** Doğrulanmış fiili kişi-saat / aynı dönemin kullanılabilir işçilik kapasitesi. Fiili kişi-saat henüz doğrulanmadıysa bunun yerine ayrı etiketli “standart saat karşılığı çıktı / kapasite” gösterilebilir; fiili kullanım diye adlandırılmaz.
- **Gerçekleşen kullanımın planlanana oranı:** Aynı kapsam ve zaman aralığındaki fiili kişi-saat / planlı kişi-saat. Bunun üretim başarısıyla aynı olmadığı belirtilir: fazla zaman tüketimi yüksek gerçekleşme anlamına gelmez. Çıktı gerçekleşme oranı birlikte gösterilir.
- Gün ortası/gün sonu gerçekleşeni tüm haftanın kapasitesiyle karşılaştırıp gecikme varmış gibi göstermemek için “kontrol gününe kadar” ve “hafta toplamı” kapsamları ayrılır. Payda sıfırsa yüzde yerine “plan yok / kapasite yok” gösterilir.

### Plan dışı üretim ve yakın ufuk

Önce stok/yarımamül + operasyon + geçerli rota/istasyon ilişkisiyle mevcut hafta planına eşleştirilir. Müşteri ve sipariş bu eşleştirmeyi kısıtlamaz. Aynı haftadaki fazla üretim de ayrı işaretlenir. Haftalık plan hedefini aşan veya bu hafta hedefi olmayan üretim için seçilebilir yakın ufukta, varsayılan dört ileri haftada, ilgili planlar aranır.

Sonuçlar ayrı gruplardır: “bu hafta planlı”, “gelecek hafta planıyla eşleşen erken üretim”, “yakın ufukta planı yok” ve “eşleştirme belirsiz”. Yakın ufuk dışında plan olup olmadığı ayrıca anlaşılabilir olmalı; “yakın ufukta yok” ifadesi tüm zamanlarda plansız olduğu anlamına gelmez.

Benzerlik, doğrulanmış ortak yarımamül/reçete ve operasyon ilişkisi üzerinden kurulmalı; benzer ad veya yakın stok kodu tek başına eşleştirme nedeni olmamalı. Bir üretim detayı iki ayrı planın tamamlanmasına birden sayılmaz. Ortak yarımamülde aday bitmiş ürünler gösterilebilir, fakat tamamı her adaya ayrı ayrı yazılmaz. Plan sürümü değişince tahsisler izlenebilir şekilde yeniden hesaplanır.

Erken üretim mevcut haftanın planlı gerçekleşme oranını yükseltmez; o haftanın toplam çıktısında ve kapasite analizinde görünür. Gelecek hedefi karşılama etkisi ayrıca gösterilir. Analitik eşleştirme, plan satırlarını veya stok rezervasyonlarını kendiliğinden değiştirmez.

### Görsel düzen ve uygulama sınırı

Basic tablonun yerine özet kartları, günlük birikimli grafik, iş merkezi durum matrisi ve açılabilir operasyon akışı birlikte kullanılacak. Plan dışı üretim için ayrı görünüm ve yakın ufuk eşleşme listesi olacak. Her grafikte birim, tarih aralığı, plan sürümü ve son MES aktarım zamanı görünür olacak. Filtrelenmiş detaylar Excel'e aktarılabilecek. Stok & Rezervasyon, üretim sonrası müşteri eşleştirmesinin sahibi olmaya devam edecek.

Bu bölüm onaylanan iş ihtiyacını ve önerilen tasarım kararlarını tarif eder. MES kimlik/miktar/zaman sorularının yanıtları gelmeden fiili kullanım, sağlam üretim düzeltmeleri veya otomatik canlı import için kesin veri politikası uygulanmaz.

Otomatik bitmiş depo girişi rota zincirindeki operasyon miktarlarının en düşüğüne dayanıyor. Ortak yarımamül, farklı reçete katsayıları, ara stoktan karşılanan işler ve MES başlangıç tarihinden önceki üretimler için bu tek başına fiziksel bitmiş stok kanıtı değildir. ERP bitmiş stok raporu ve MES tamamlanması birlikte kullanılırsa çift sayım önlenmeli.

## Gerekli uygulama çalışması

1. **MES dosya adaptörü:** Üreticiye özgü stil hatasını yönet; gerekli sütunları denetle; gerçek hücre tarihlerini oku; kaynak dosya kimliği ve ham satırı koru. Okunamayan dosyada başarı raporlama.
2. **Kaynak detay tablosu:** Kaynak sistem/tesis + Üretim Detay Id temelinde benzersiz kayıt tut. Üretim Id, iş emri, rapor zamanı, makine, yarımamül, bitmiş ürün, tarihler, kalite miktarları ve süreleri sakla. Sipariş/müşteri/poz sadece isteğe bağlı kaynak bilgisi olsun. Aynı detay tekrar gelince yeni üretim ekleme; son sürümle güncelle. Dosyada görünmeyen kaydı silme; iptal yalnızca açık kaynak sinyaliyle işlenmeli.
3. **Müşteri bağımsız eşleştirme ve havuz:** MES makinesi → uygulama makinesi → ana/alternatif rota istasyonu; malzeme kodu → yarımamül operasyonu; varsa Bitmiş Ürün → ürün eşleştirmesini uygula. Üretimi ürün/operasyon havuzunda tut. Müşteri veya sipariş yokluğu engel olmasın. Tanınan ortak yarımamülü koru; bitmiş ürün ataması belirsizse bu atamayı beklet. Hiç tanınmayan üretim kaydını ham kaynak katmanında hata durumuyla sakla.
4. **Miktar politikası:** Detay üretimi ile iş emri toplamını ayır. Brüt, sağlam, hurda ve rework'ü ayrı tut. Negatif/geriye dönük düzeltmeleri kaynak detaya bağlı işle. Kalan iş, Gantt, KPI ve stokta aynı sağlam üretim tanımını kullan.
5. **Süre politikası:** Dakikaları saate dönüştür. Net süreyi otomatik olarak insan çalışma süresi sayma; makine süresi, operatör kişi sayısı ve eşzamanlı çalışma anlamı doğrulanmalı. Rapor çevrim süresi ana rota standardını kendiliğinden değiştirmemeli.
6. **İçe aktarma önizlemesi:** Yeni, güncellenen, değişmeyen, belirsiz, hatalı ve tarih dışı kayıtları göster. Ürün/operasyon bazında önceki ve yeni miktar farklarını göster. Onaylı işlemi tek transaction ile uygula; satır bazlı hata/uyarıları Excel'e çıkar.
7. **Plan sürümü ve analitik servis:** Haftalık planın karşılaştırma sürümünü sakla; ürün/operasyon hedeflerini müşteri ayrımı olmadan birleştir. Kontrol gününe göre günlük/birikimli üretim, kalan miktar, tahmini kişi-saat ve kapasite oranlarını hesapla. Mevcut sipariş bazlı kalan iş fonksiyonunu doğrudan temel alma; ürün havuzu ve stok netleştirmesini uzlaştır.
8. **Plan dışı / yakın ufuk servisi:** Üretimi haftalık hedefe bir kez say; fazlayı ve plansız üretimi ileri haftalara aday eşleştirmeleriyle göster. Erken üretim, eşleşmeyen ve belirsiz kayıtları ayrıştır; üretim, plan ve stok arasında çift sayımı önle.
9. **`/progress` arayüzü:** Özet kartları, günlük grafikler, iş merkezi matrisi, ürünün operasyon zinciri, kalan miktar/saat ve plan dışı üretim görünümünü uygula. İş merkezi/stok/operasyon filtreleri ve Excel aktarımını ekle. Son veri zamanı, kullanılan plan sürümü ve hesaplanamayan metrikleri görünür kıl.
10. **Kalan planı güncelleme:** Analitik sonuçları ve doğrulanmış stokları uzlaştırdıktan sonra etkilenen kalan işleri planlama ön kontrolünden geçir; yeniden çizelgelemeyi ayrı, kullanıcı tarafından görülen bir adım yap. Üretim sonrası müşteri eşleştirmesini Stok & Rezervasyon'a bırak.
11. **Kabul testleri:** Aynı dosyanın tekrar yüklenmesi, gün içi rapor, gece vardiyası, geçmiş düzeltmesi, negatif sağlam, ortak yarımamül, eksik müşteriyle geçerli üretim, birden çok siparişin aynı ürün hedefi, ileri hafta erken üretimi, fazla üretim, plan değişimi ve başlangıç stoklarıyla toplamların korunması. Günlük plan yokluğu, sıfır payda ve kişi-saat/makine-saati ayrımını da doğrula.

## MES tarafında netleştirilmesi gerekenler

- Üretim Detay Id aynı kaydın güncellemelerinde sabit mi; kimlik tesisler arasında tekil mi? Silinen/iptal edilen detay nasıl bildiriliyor?
- Üretilen, Net Üretilen, Sağlam Adet ve İş Emri Bazında Toplam Üretilen alanlarının kesin tanımları nedir? Negatif sağlam adet geçmiş üretim düzeltmesi mi?
- Tarih alanı vardiya günü mü, takvim günü mü? 04.08.2026 satırının bu raporda bulunma nedeni nedir? Tam rapor mu, değişiklik listesi mi?
- Bitmiş Ürün boşken üretimi yarımamül havuzunda tutmak ve bitmiş ürün adaylarını doğrulamak için hangi reçete/üretim emri bilgisi kullanılabilir? Sipariş/poz ve müşteri eşleştirmesi üretim raporunun ön koşulu değildir; Lot No ilişkisi yalnızca isteğe bağlı izlenebilirlik konusu olarak kalır.
- Net süre makine süresi mi? Çalışan personel sayısı o detayın tamamına ait fiili ekip mi? Başlangıç yarımamül/bitmiş stok ve günlük bitmiş stok için esas sistem hangisi?

Bu sorular çözülmeden otomatik canlı importu açmak yerine önce önizlemeli adaptör ve günlük uzlaştırma raporu uygulanmalıdır.
