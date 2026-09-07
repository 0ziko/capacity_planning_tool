# Aktif Bağlam

## Şu Anki Odak
İlk çalışan sürüm (v0.1) tamamlandı: spec'teki tüm ana kalemler uçtan uca çalışıyor (backend API + React arayüz + testler). Sıradaki adım: **gerçek pilot verisiyle deneme** ve kullanıcı geri bildirimi.

## Son Değişiklikler (2026-09-07)
- Teknoloji kararı alındı: Python/FastAPI + PostgreSQL (yerelde SQLite) + React/Vite SPA, önce localhost.
- Ortam kuruldu: Python 3.12, Node 24 (portable), Git 2.55 (hepsi kullanıcı kapsamı, admin yok).
- Backend yazıldı: modeller, kapasite/ihtiyaç/planlama/ilerleme/analiz servisleri, Excel import (9 tür) + şablonlar + tek tık yedek, JWT auth + 3 rol, API.
- Frontend yazıldı: 10 sayfa (Özet, İş Merkezleri, Personel, Stok/BOM/Rota, Siparişler & İhtiyaç, Planlama, Günlük İlerleme, Duruş & Çevrim Süresi, Excel Import/Yedek, Kullanıcılar).
- Testler: `tests/test_capacity_flow.py` spec örneğini doğruluyor (10×4×5 = 200 saat = 20 birim; otomatik plan 200 + 77.8; ilerleme 40/40; fazla duruş 100 dk; terminleme; tüm Excel çıktıları). 2/2 geçiyor.
- Demo veri (`seed_demo.py`) yüklendi; arayüz tarayıcıda doğrulandı (özet, planlama, terminleme).
- İlerleme durumu: 0 geçmiş gün varsa "Başlamadı" (not_started) gösterilir.

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
