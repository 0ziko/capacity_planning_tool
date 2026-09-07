# Aktif Bağlam

## Şu Anki Odak
v0.3: kullanıcı kontrolleri sürüyor; sipariş odaklı planlama görünümleri (bitiş tarihi, İM bazlı, ilerleme, birleştirme) + birim fiyat/ciro raporu + iki planlama modu (termin / maksimum ciro) ve karşılaştırma eklendi. Kullanıcı kendi verisini (PRS3 iş merkezi, 6005510 stoku, "Deneme" siparişi — birim fiyat 1850 girildi) girerek test ediyor. Sıradaki adım: kullanıcının yeni geri bildirimleri.

## Son Değişiklikler (2026-09-07)
- Teknoloji kararı alındı: Python/FastAPI + PostgreSQL (yerelde SQLite) + React/Vite SPA, önce localhost.
- Ortam kuruldu: Python 3.12, Node 24 (portable), Git 2.55 (hepsi kullanıcı kapsamı, admin yok).
- Backend yazıldı: modeller, kapasite/ihtiyaç/planlama/ilerleme/analiz servisleri, Excel import (9 tür) + şablonlar + tek tık yedek, JWT auth + 3 rol, API.
- Frontend yazıldı: 10 sayfa (Özet, İş Merkezleri, Personel, Stok/BOM/Rota, Siparişler & İhtiyaç, Planlama, Günlük İlerleme, Duruş & Çevrim Süresi, Excel Import/Yedek, Kullanıcılar).
- Testler: `tests/test_capacity_flow.py` spec örneğini doğruluyor (10×4×5 = 200 saat = 20 birim; otomatik plan 200 + 77.8; ilerleme 40/40; fazla duruş 100 dk; terminleme; tüm Excel çıktıları). 2/2 geçiyor.
- Demo veri (`seed_demo.py`) yüklendi; arayüz tarayıcıda doğrulandı (özet, planlama, terminleme).
- İlerleme durumu: 0 geçmiş gün varsa "Başlamadı" (not_started) gösterilir.
- Kullanıcı geri bildirimi (11:06): iş merkezi çoklu seçimi (native <select multiple>) kullanışsızdı → `WcMultiSelect` aranabilir açılır liste + onay kutuları + chip + Tümünü seç/Temizle olarak yeniden yazıldı (Siparişler, Planlama, Günlük İlerleme, Duruş & Çevrim Süresi aynı bileşeni kullanır). Kullanıcı kontrollerine ve kendi verisini girmeye başladı (PRS3 iş merkezi, Deneme siparişi).

- Kullanıcı isteği (11:22, 5 madde) tamamlandı:
  1. Siparişler sayfasında tekil sipariş formu (ekle/düzenle/sil; stok kodu datalist ile aranır) → `POST/PUT/DELETE /api/orders`.
  2. Plan sonucu sipariş bitiş tarihleri → `GET /api/plan/orders` (`services/orders.order_schedule`): ihtiyaç/planlanan saat, kapsam %, ilk hafta, tahmini bitiş günü (son haftada İM doluluk sırasına göre gün tahmini), sapma (gün), durum (on_time/late/partial/unplanned/no_ops). Plan Excel'ine "Sipariş Bitiş Tarihleri" sayfası eklendi.
  3. Planlama sayfası sekmeli yapıya geçti (`pages/planning/*`): Haftalık yük & plan satırları · Sipariş bitiş tarihleri · İş merkezi bazlı siparişler (İM sekmeleri, nihai ürün termini + tahmini bitiş) · Sipariş ilerleme · Birleştirme önerileri · Yeni iş terminleme.
  4. Sipariş/iş emri ilerlemesi → `GET /api/progress/orders` (+ `.xlsx`): üretim kayıtları sipariş no+stok+operasyon ile eşleşir; sipariş no boşsa aynı stokun açık siparişlerine termin sırasıyla FIFO dağıtılır; sipariş no dolu ama açık sipariş yoksa dağıtılmaz. Butonla tetiklenir, operasyon detayı açılır satırda.
  5. Birleştirme → `GET /api/plan/merge-suggestions`, `POST /api/plan/merge`, `DELETE /api/plan/merge/{id}`: aynı stok kodlu açık siparişler gruplanır; seçilenler tek siparişte toplanır (miktar toplamı, en erken termin, müşteriler "A + B", not alanında kaynaklar), kaynaklar `status=merged` + `merged_into_id`; plan satırları silinir, geri alınabilir.
- Şema: `orders.merged_into_id`, `orders.note` eklendi; `db/migrate.ensure_columns` mevcut tablolara eksik kolonları ALTER TABLE ile ekler (Alembic'e kadar).
- Düzeltme: `DELETE /api/orders?status=` toplu silmede plan satırları artık elle temizleniyor (ORM cascade toplu silmede çalışmıyordu).
- Testler: `tests/test_orders_flow.py` (3 test) eklendi; toplam 6/6.

- Kullanıcı isteği (11:49, 2 madde) tamamlandı (v0.3):
  1. Birim fiyat → `orders.unit_price` (ciro = miktar × birim fiyat; para birimi tek kabul). Sipariş formu/listesi, sipariş Excel şablonu ("Birim Fiyat" kolonu, alias fiyat/birimfiyat/satisfiyati) ve yedek çıktısı güncellendi. Birleştirmede ağırlıklı ortalama fiyat. Ciro raporu → `GET /api/plan/revenue` (`services/revenue.revenue_report`): haftalık + aylık; **tamamlanan** (siparişin tahmini bitiş günü hangi döneme düşüyorsa tüm cirosu orada) ve **oransal** (plan satırı saat payına göre dağıtım), kümülatifler; toplam açık / ufukta tamamlanan / kısmi / planlanmayan ciro. Planlama › **Ciro** sekmesi; plan Excel'ine "Ciro (Haftalık)" ve "Ciro (Aylık)" sayfaları; Sipariş bitiş tarihleri tablosuna Ciro kolonu.
  2. İki planlama modu → `AutoPlanRequest.mode`: `due_date` (termin sırası, eski davranış) / `revenue` (ufukta maksimum ciro). Motor `planning.simulate()` olarak kalıcı olmayan hale getirildi; `auto_plan` sonucu yazar (`plan_lines.strategy` kolonu). Ciro modu: siparişler saat başına ciroya (ciro ÷ gereken saat) göre sıralanır, yalnızca ufka **tamamen** sığanlar alınır (ciro teslimde gerçekleşir), kalan kapasite atlananlarla termin sırasıyla kısmen doldurulur; tamamen dışarıda kalanlar `skipped`. Karşılaştırma → `POST /api/plan/compare` (`services/revenue.compare`): iki simülasyon + sipariş çizelgesi + ciro raporu; sipariş bazlı fark sınıfı (`rev_misses_due`: ciro planında termin kaçar · `rev_drops`: ciro planı dışarıda bırakır · `due_drops`: termin planı ufka sığdıramaz, ciro planı bitirir · rev_earlier/rev_later/same). Planlama › **Plan karşılaştır** sekmesi: iki senaryo kartı (ciro, termine uygun, geç, gecikme günü, kısmi/planlanmadı, doluluk; farklar renkli), özet fark listesi, dönemsel ciro yan yana, sipariş tablosu (filtre butonları), "Bu planı uygula" (poweruser). Üst panelde "Planlama modu" seçimi.
- Şema: `orders.unit_price`, `plan_lines.strategy` (ensure_columns ile eklenir).
- Testler: `tests/test_revenue_modes.py` (2 test: fiyat import/form; 200 saatlik tek İM'de üç siparişle termin vs ciro modu, karşılaştırma listeleri, ciro raporu, strategy, Excel) → toplam 8/8. tsc temiz. Tarayıcıda Siparişler formu (canlı ciro alanı), Ciro sekmesi ve Plan karşılaştır (geçici test siparişleriyle fark senaryosu) doğrulandı; geçici siparişler silindi.
- Olay (13:01): kullanıcı giriş yapamadı — backend tamamen yanıt vermiyordu. py-spy ile bakıldı: ana thread `logging.emit` içinde stderr'e traceback yazarken bloke olmuştu (backend, Cursor araç kabuğu altında `2>&1` ile çalışıyordu; kabuğun stderr borusu okunmaz hale gelince ilk 500 hatasının traceback'i tüm event loop'u kilitledi). Tetikleyen 500: kullanıcı demo iş merkezlerini silmişti; SQLite FK denetlemediği için demo stokların rota operasyonları silinmiş İM'ye işaret eden yetim kayıt oldu → `/api/requirements/item` içinde `op.work_center.code` AttributeError.
  Düzeltmeler: (1) backend artık `backend/start_backend.ps1` ile bağımsız süreç olarak, çıktılar `backend/logs/backend.{out,err}.log` dosyalarına yazılarak çalışıyor (-Stop ile durdur); (2) İş merkezi silme: bağlı rota/plan/üretim/duruş kaydı varsa 400 + açıklama ("Pasif yapın"); (3) SQLite bağlantısında `PRAGMA foreign_keys=ON`; (4) açılışta `repair_orphans` yetim satırları temizler ve loglar (kullanıcı DB'sinde 10 rota + 3 üretim + 3 duruş temizlendi); (5) servisler `work_center is None` durumuna toleranslı. `tests/test_integrity.py` (2 test) → toplam 10/10.
## Sonraki Adımlar
1. Kullanıcıdan gerçek Excel dosyalarının kolon yapısını al → `excel.py` TEMPLATES alias listesini genişlet.
2. Pilot iş merkezleriyle deneme; verimli saat / vardiya tanımlarını gerçek değerlerle doğrula.
3. PostgreSQL kurulumu (admin gerektirir; IT ile) → `.env` DATABASE_URL değişikliği yeterli.
4. Alembic ile şema migrasyonu (şema değişmeye başlayınca).
5. Geliştirme adayları: haftalık plan ekranında sürükle-bırak; ürün grubu bazlı çevrim süresi önerisini rota tanımına "uygula" butonu; MRP2'den sipariş Excel'inin doğrudan alias eşlemesi; kullanıcı bazlı iş merkezi kısıtı (yetki matrisi detayı).

## Açık Sorular (kullanıcıya)
- Gerçek import dosyalarının (sipariş, BOM/rota, personel, üretim, duruş) örnekleri?
- Vardiya düzenleri haftadan haftaya değişiyor mu? (Şu an sabit tanım; tarih bazlı istisna yok.)
- Çevrim süresi birimi sn/adet varsayıldı; setup dakika. Doğru mu?
- Duruş "beklenen" tanımı = (nominal − verimli) × kişi. Kabul edilebilir mi, yoksa ayrı bir beklenen duruş tablosu mu istenir?
- Terminlemede aynı gün içinde operasyonların ardışıklığı basitleştirildi (verimli saat → nominal saate oransal yayılır). Yeterli mi?

## Önemli Tercihler / Desenler
- Kullanıcı Türkçe; arayüz, hata mesajları, Excel başlıkları Türkçe. Kod tanımlayıcıları İngilizce.
- Excel-merkezli akış: her tablo için şablon indir → doldur → yükle; yedek dosyası şablonlarla aynı formatta (geri yüklenebilir).
- Pilot: `WorkCenter.is_planned` bayrağı; otomatik plan yalnızca işaretli iş merkezlerini doldurur.
- Import'lar idempotent (kod/anahtar bazlı upsert; üretim satırı gün+iş merkezi+stok+op+sipariş anahtarlı; duruşta gün+iş merkezi yeniden yüklenirse eskiler silinir).
- Memory bank dosyaları UTF-8 (BOM'suz) tutulur; Write aracı UTF-16 üretirse dönüştür (bkz. techContext).

## Öğrenimler
- Spec örneği (200 saat / 20 birim / günlük 40 saat) test vakası olarak kodlandı; hesap mantığı değişirse test de güncellenmeli.
- Otomatik planlamada operasyon sırası: sonraki operasyon, öncekinin başladığı haftadan önce başlayamaz (aynı hafta serbest). Bu, pilot için yeterli kabul edildi.
- Ciro modunda "ufka tamamen sığma" şartı önemli: kısmi yerleştirilen bir sipariş ciro üretmez, bu yüzden açgözlü sıralama (ciro/saat) + tam sığma denemesi (kapasite kopyası üzerinde) + kalanı termin sırasıyla doldurma yaklaşımı seçildi. Fiyatı 0 olan siparişler ciro modunda en sona düşer.
- Oransal ciroda pay, tamamen planlanan siparişlerde planlanan toplam saate göre alınır (yuvarlama farkı toplam cirodan sapmasın).
- Cursor `Read`/`StrReplace` araçları memory-bank .md dosyalarını (UTF-8, BOM'suz olsa da) bozuk okuyor; içerik için `Get-Content -Encoding UTF8` / Grep, düzenleme için küçük bir Python betiği kullan.
