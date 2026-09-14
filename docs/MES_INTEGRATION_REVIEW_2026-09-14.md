# MES üretim raporu — entegrasyon incelemesi

Kaynak: `Uretim_Kayitlari_Raporu_14 Eylül 2026 17_38.xlsx`. İnceleme tarihi: 14.09.2026. Karşılaştırma, aynı tarihteki yerel rota ve makine tanımlarına karşı salt okunur yapıldı. Dosya üretim veritabanına yüklenmedi.

## Sonuç

Bu rapordan operasyon bazında üretim, sağlam/hurda miktarları ve gerçekleşen süreler çıkarılabilir. Ancak dosya mevcut uygulamaya **olduğu gibi, güvenilir biçimde import edilemiyor**. Ayrı bir MES adaptörü, kalıcı kaynak kayıt kimlikleri, kontrollü rota eşleştirmesi ve miktar politikası gerekir. Mevcut genel üretim importuna yalnızca başlık dönüştürerek bağlamak yeterli değildir.

Günlük üretim akışı, başlangıç yarımamül ve bitmiş stokları, doğru rota/reçete miktarları, güncel siparişler ve iş gücü takvimiyle birlikte kalan işi güncelleyebilir. Tek günlük rapor, önceki günlerden devreden üretimi veya depoda fiziksel olarak kullanılabilir miktarı tek başına kanıtlamaz. Güncel kalan iş ile mevcut planın tekrar çizelgelenmesi ayrı adımlardır; veri importu tek başına planı otomatik yeniden oluşturmaz.

## Dosyanın somut bulguları

- 139 veri satırı ve 139 farklı Üretim Detay Id var; Üretim Id ise 125 farklı değer içeriyor. İlk örnekte detay kimliği tekil; kimliğin farklı gün/tesislerde de tekil olduğu MES sözleşmesinden doğrulanmalı.
- Tarih sütununda 14.09.2026 için 133, 13.09.2026 için 5, 04.08.2026 için 1 satır var. Dosya adı üretim tarihi yerine kullanılamaz.
- 69 satırda Bitmiş Ürün boş; 69 satırda Sipariş No boş. Bu alanlar olmadan belirli bir müşteri siparişinin fiziksel olarak tamamlandığını söylemek mümkün değildir.
- Tarih + malzeme kodu + sipariş anahtarında 16 grup birden fazla detay içeriyor.
- Malzeme kodu ve varsa Bitmiş Ürün ipucuyla mevcut yarımamül rota indeksinde 65 satır tek aday, 60 satır birden fazla aday, 14 satır sıfır aday verdi. Bu, bu iki alanla yapılan aday taramasıdır; tam MES eşleştirme başarısı veya 65 satırın koşulsuz import edilebilirliği anlamına gelmez. Makine, operasyon ve sipariş/poz kontrolleri ayrıca gerekir.
- MES İş Merkezi Kodu, çoğunlukla uygulamadaki **makine kodudur**: HP-60 gibi. 139 satırın 138'inin kodu makine tablosunda bulunuyor. Makinenin tanınması, o rotada çalışmaya yetkili olduğunun kanıtı değildir; ana/alternatif istasyon kontrolü gerekir.
- İş emri durumları: 68 Bekliyor, 68 Üretimde, 3 Tamamlandı. Bekliyor durumunda da üretim miktarı olan kayıtlar var; durum alanı üretim miktarını geçersiz saymak için kullanılmamalı.
- Dosyanın stil tanımı mevcut openpyxl okuyucusunda `expected <class 'openpyxl.styles.fills.Fill'>` hatası veriyor. İnceleme için ham XLSX XML verisi okundu; kaynak dosya değiştirilmedi. MES adaptörü bu üretici biçimini desteklemeli veya üreticinin geçerli XLSX çıktısı sağlaması gerekir.

## Doğru ve yanlış hesap örnekleri

`5000983-21` yarımamülü, `6000983` bitmiş ürünü, `W00000000385014` siparişi için aynı gün dört detay var: 0, 0, 173 ve 44. Detay üretim toplamı **217**. İş emri bazında toplam üretilen miktar her satırda 217 olduğu için bu sütunun toplamı 868 olur ve yanlıştır. Mevcut gün/yarımamül/sipariş üzerine yazma yaklaşımıyla son satırın 44 adedi kalabilir; bu da yanlıştır. MES operasyon kodu 21, mevcut rotadaki sıra ise 10: MES operasyon kodu doğrudan uygulamanın operasyon sırası değildir.

Bu 217 adet delik delme operasyonunun üretimidir; aynı sayıda bitmiş evyenin bütün operasyonlarının tamamlandığını göstermez. Sipariş yüzdesi için doğru sipariş/poz talebi, önceki üretimler ve her gerekli operasyonun tamamlanması değerlendirilmelidir.

`5011261-12`, detay 5294: Üretilen Miktar 287, Net Üretilen Miktar 286, Sağlam Adet 286, Hurda 1. Kapasiteden düşülecek tamamlanma miktarı için brüt üretim ile sağlam üretim ayrılmalıdır.

`5005512-03`, detay 5221: Üretilen Miktar 0, Net Üretilen Miktar 0, Sağlam Adet -2, Hurda 2. Bu satır sessizce sıfıra yuvarlanmamalı veya negatif bitmiş stok yaratmamalı. Geriye dönük kalite düzeltmesi mi, başka üretim detayına bağlı hurda mı olduğu doğrulanmalıdır.

Tüm dosyada Üretilen Miktar toplamı 10.363, Net Üretilen Miktar 10.362, Sağlam Adet 10.360, Hurda 3. Bunlar **farklı ürün ve operasyonların satır toplamlarıdır**, bitmiş ürün adedi değildir. Sağlam toplamı negatif düzeltme satırını içerir; doğrulanmadan stok kabulü için kullanılmamalıdır. İş emri toplam sütununun satır toplamı 40.629'dur ve tekrarlar nedeniyle günlük çıktı olarak kullanılamaz.

## Mevcut programın karşılayabildiği ve karşılayamadığı noktalar

Genel üretim importu tarih, yarımamül, bitmiş stok ipucu, iş merkezi, operasyon sırası, sipariş ve miktarı alabiliyor. Üretim kayıtlarında ayrıca iyi adet, hurda, rework ve süre temeli alanları bulunuyor. Ancak MES detay kimliği ve iş emri kimliği için ayrı kaynak kayıt modeli bulunmuyor. Dolayısıyla aynı detayın tekrar yüklenmesini, gün içi artışını, düzeltmesini ve iptalini kaynak kimliğinden takip edemiyor.

Yarımamül birden çok rotada kullanılıyorsa mevcut akış plan/termin önceliğine göre seçim veya dağıtım yapabiliyor. Bu bir planlama varsayımıdır; MES'te üretimin gerçekte hangi bitmiş ürüne ait olduğuna dair izlenebilirlik değildir. Yeni adaptör belirsiz eşleşmeyi onaysız biçimde böyle dağıtmamalıdır.

`remaining_work.produced_qty_map` kalan işi hesaplarken `ProductionActual.quantity` kullanıyor; stok üretim zinciri hesabı da miktara dayanıyor. Bazı KPI hesapları `good_qty` alanını kullanıyor. Brüt üretimi quantity'ye, sağlamı yalnızca good_qty'ye koymak mevcut durumda tamamlanmayı ve stok hesabını tutarsız yapabilir. Sağlam üretim ve düzeltme politikası bütün tüketicilerde birlikte düzenlenmeli.

Sipariş no + bitmiş ürün birden fazla açık poza uyuyorsa mevcut kalan iş hesabı uyarı verip otomatik atamıyor. Dosyada Lot No var; bunun ERP poz numarası olduğuna dair bir sözleşme yok. Sipariş pozuyla eşit olduğu varsayılmamalı.

Otomatik bitmiş depo girişi rota zincirindeki operasyon miktarlarının en düşüğüne dayanıyor. Ortak yarımamül, farklı reçete katsayıları, ara stoktan karşılanan işler ve MES başlangıç tarihinden önceki üretimler için bu tek başına fiziksel bitmiş stok kanıtı değildir. ERP bitmiş stok raporu ve MES tamamlanması birlikte kullanılırsa çift sayım önlenmeli.

## Gerekli uygulama çalışması

1. **MES dosya adaptörü:** Üreticiye özgü stil hatasını yönet; gerekli sütunları denetle; gerçek hücre tarihlerini oku; kaynak dosya kimliği ve ham satırı koru. Okunamayan dosyada başarı raporlama.
2. **Kaynak detay tablosu:** Kaynak sistem/tesis + Üretim Detay Id temelinde benzersiz kayıt tut. Üretim Id, iş emri, rapor zamanı, makine, yarımamül, bitmiş ürün, sipariş/poz, tarihler, kalite miktarları ve süreleri sakla. Aynı detay tekrar gelince yeni üretim ekleme; son sürümle güncelle. Dosyada görünmeyen kaydı silme; iptal yalnızca açık kaynak sinyaliyle işlenmeli.
3. **Kontrollü eşleştirme:** MES makinesi → uygulama makinesi → ana/alternatif rota istasyonu; malzeme kodu → yarımamül operasyonu; Bitmiş Ürün → ürün; sipariş ve doğrulanmış poz → talep eşleştirmesini uygula. Sıfır/çok adayları ayrı listele; kullanıcı çözmeden üretime aktarma.
4. **Miktar politikası:** Detay üretimi ile iş emri toplamını ayır. Brüt, sağlam, hurda ve rework'ü ayrı tut. Negatif/geriye dönük düzeltmeleri kaynak detaya bağlı işle. Kalan iş, Gantt, KPI ve stokta aynı sağlam üretim tanımını kullan.
5. **Süre politikası:** Dakikaları saate dönüştür. Net süreyi otomatik olarak insan çalışma süresi sayma; makine süresi, operatör kişi sayısı ve eşzamanlı çalışma anlamı doğrulanmalı. Rapor çevrim süresi ana rota standardını kendiliğinden değiştirmemeli.
6. **İçe aktarma önizlemesi:** Yeni, güncellenen, değişmeyen, belirsiz, hatalı ve tarih dışı kayıtları göster. Ürün/operasyon bazında önceki ve yeni miktar farklarını göster. Onaylı işlemi tek transaction ile uygula; satır bazlı hata/uyarıları Excel'e çıkar.
7. **Kalan planı güncelleme:** Doğrulanmış üretimi operasyonlara yansıt; başlangıç yarımamül stoğunu ve bitmiş stok kaynağını uzlaştır. Sonrasında etkilenen kalan işleri planlama ön kontrolünden geçir; plan yeniden çizelgelemesini ayrı, kullanıcı tarafından görülen bir adım yap.
8. **Kabul testleri:** Aynı dosyanın iki kez yüklenmesi, gün içi iki rapor, ertesi güne taşan üretim, geçmiş tarih düzeltmesi, sıfır miktarlı durum kaydı, negatif sağlam adet, birden çok poz, ortak yarımamül ve kalite farkında toplamların korunması.

## MES tarafında netleştirilmesi gerekenler

- Üretim Detay Id aynı kaydın güncellemelerinde sabit mi; kimlik tesisler arasında tekil mi? Silinen/iptal edilen detay nasıl bildiriliyor?
- Üretilen, Net Üretilen, Sağlam Adet ve İş Emri Bazında Toplam Üretilen alanlarının kesin tanımları nedir? Negatif sağlam adet geçmiş üretim düzeltmesi mi?
- Tarih alanı vardiya günü mü, takvim günü mü? 04.08.2026 satırının bu raporda bulunma nedeni nedir? Tam rapor mu, değişiklik listesi mi?
- Bitmiş Ürün boşken gerçek ürün hangi kaynaktan bulunabilir? Lot No gerçekten sipariş pozuna karşılık geliyor mu?
- Net süre makine süresi mi? Çalışan personel sayısı o detayın tamamına ait fiili ekip mi? Başlangıç yarımamül/bitmiş stok ve günlük bitmiş stok için esas sistem hangisi?

Bu sorular çözülmeden otomatik canlı importu açmak yerine önce önizlemeli adaptör ve günlük uzlaştırma raporu uygulanmalıdır.
