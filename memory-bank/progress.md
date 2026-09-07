# İlerleme

## Çalışan (v0.1 — 2026-09-07)
- [x] Çok kullanıcılı web uygulaması (FastAPI + React SPA), JWT oturum
- [x] Yetki matrisi: admin / poweruser / user (`core/deps.py` → `require_role`), kullanıcı yönetimi ekranı
- [x] SQL şeması (PostgreSQL hedef, SQLite yerel) — 12 tablo
- [x] Excel import altyapısı: 9 tür (iş merkezi, vardiya, personel, stok, BOM, rota, sipariş, üretim, duruş), Türkçe başlık + alias eşleme, şablon indirme, hata satır raporu, import logu
- [x] Tek tık Excel yedek (tüm tablolar, şablon uyumlu sayfalar)
- [x] İş merkezi tanımları: çalışma günleri, vardiyalar (saat, kişi, kişi başı verimli saat), kapasite birimi, pilot (Planlanıyor) bayrağı
- [x] Personel listesi ve iş merkezi ataması (vardiyada kişi sayısı 0 ise personelden sayılır)
- [x] Haftalık verimli kapasite (saat + birim), gün kırılımı
- [x] BOM + rota + çevrim süresi; stok detay ekranı; miktar için toplam süre hesabı
- [x] Sipariş listesi; iş merkezi bazlı iş gücü ihtiyacı; çoklu stok / iş merkezi / termin aralığı filtresi
- [x] Otomatik planlama (termine göre, kapasite dolunca sonraki haftaya taşma, manuel satırlar korunur, operasyon sırası kısıtı)
- [x] Manuel planlama (satır ekle, haftaya taşı, saat değiştir, sil)
- [x] Haftalık yük tablosu (doluluk %, birim), plan Excel'i
- [x] Günlük üretim importu → planlanan / beklenen / gerçekleşen / kalan saat-gün; gün gün kümülatif
- [x] Duruş importu → beklenen vs gerçekleşen, fazla duruş, sebep kırılımı; Excel raporu
- [x] Yeni iş terminleme (saat bazlı operasyon başlangıç/bitiş, mevcut plan doluluğu düşülür)
- [x] Çevrim süresi öneri motoru (medyan bazlı, örnek/eşik parametreli, ürün grubu özeti) + Excel raporu
- [x] Testler (uçtan uca akış + rol kontrolü) — 2/2
- [x] Demo veri scripti (`backend/seed_demo.py`)

## Çalışan (v0.2 — 2026-09-07, kullanıcı geri bildirimi sonrası)
- [x] İş merkezi çoklu seçimi: aranabilir açılır liste + chip (tüm sayfalarda ortak bileşen)
- [x] Tekil sipariş ekleme / düzenleme / silme (Siparişler sayfası)
- [x] Sipariş bazlı tahmini üretim bitiş tarihi ve termine göre durum (Planlama › Sipariş bitiş tarihleri; plan Excel'inde sayfa)
- [x] İş merkezi bazlı planlanmış siparişler görünümü (Planlama › İş merkezi bazlı)
- [x] Sipariş / iş emri ilerleme raporu (Planlama › Sipariş ilerleme; butonla tetiklenir; Excel)
- [x] Aynı stok kodlu siparişleri birleştirme önerisi + birleştir / geri al (Planlama › Birleştirme önerileri)
- [x] Hafif şema migrasyonu (eksik kolon ekleme) — Alembic'e kadar

## Yapılacaklar
- [ ] Gerçek Excel formatlarına göre alias/şablon uyarlaması
- [ ] PostgreSQL kurulumu ve `.env` geçişi (admin/IT)
- [ ] Alembic migrasyonları
- [ ] Çevrim süresi önerisini rotaya "uygula" aksiyonu
- [ ] Tarih bazlı vardiya istisnaları (tatil, fazla mesai günü)
- [ ] Kullanıcı bazlı iş merkezi görünürlüğü (yetki matrisi detayı)
- [ ] Üretim ortamı: uvicorn servis olarak, frontend `npm run build` çıktısının sunulması, HTTPS

## Mevcut Durum
Pilot denemeye hazır. Yerelde çalışıyor: backend :8000, frontend :5173 (SQLite).

## Bilinen Sorunlar / Sınırlamalar
- Terminleme, gün içi ardışıklığı yaklaşık hesaplar (verimli saat → nominal saate oransal).
- Sipariş bitiş tarihi hafta granüler plandan gün tahminidir (son haftada İM'nin termin sıralı doluluğuna göre); gün bazlı çizelgeleme değildir.
- Sipariş ilerlemesinde sipariş no'suz üretim FIFO dağıtılır; sipariş no verilmiş ama açık sipariş yoksa (kapalı/birleşik) kayıt hiçbir siparişe yazılmaz.
- Otomatik plan operasyonları hafta granülerliğinde sıralar; gün bazlı ardışıklık yok.
- Çevrim süresi önerisi "fiili süre" yoksa günün verimli kapasitesini kazanılan saat oranıyla paylaştırır — kaba bir tahmindir; fiili süre kolonu doldurulursa doğruluk artar.
- SECRET_KEY varsayılanı kısa; üretimde `.env` içinde uzun rastgele değer verilmeli.

## Karar Evrimi
- 2026-09-07: MRP2 sonlu kapasite yerine bağımsız iş gücü kapasite planlama aracı (veri akışı yetersizliği).
- 2026-09-07: Yığın: Python/FastAPI + PostgreSQL + React SPA; yerelde SQLite fallback (PostgreSQL admin gerektirdiği için).
- 2026-09-07: UI kütüphanesi kullanılmadı (bağımlılık azlığı, tablo odaklı sade arayüz).
