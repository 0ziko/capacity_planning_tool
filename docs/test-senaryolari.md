# Entegre Test Senaryoları (E2E)

Projenin tüm bileşenlerini birlikte çalıştıran senaryo paketi. Üç katman vardır:

| Katman | Nerede | Veri | Ne yapar |
|---|---|---|---|
| Backend birim/servis testleri | `backend/tests/` | izole SQLite | Mevcut 350+ test; algoritma ve servis sözleşmeleri |
| **E2E senaryo paketi** | `backend/tests/e2e/` | izole SQLite (TestClient) | Excel import → master → sipariş → plan → üretim/MES → stok → revizyon → yedek → owner akışlarını API üzerinden uçtan uca |
| Canlı duman testi | `backend/tests/e2e/live_smoke.py` | **canlı PostgreSQL, salt okunur** | Ayaktaki backend/frontend'de 50+ GET ve saf hesaplama uç noktası; canlı veriyi değiştirmez |

Tek komut: `cd backend; .\run_e2e.ps1` (isteğe bağlı `-SkipUnit`, `-SkipLive`).
Raporlar `outputs/e2e/` altına yazılır: `e2e_results.md/json` (adım adım senaryo sonuçları), `live_smoke.md/json`, JUnit XML ve loglar.

## E2E senaryo kataloğu

Sabit planlama haftası `WEEK = 2026-10-05` (Pazartesi). Ortak master veri (`conftest.master`):
E2E-PRS 10 kişi × 4 saat × 5 gün = **200 saat/hafta**, E2E-MNT 5 × 4 × 5 = **100 saat**;
E2E-MAM rotası 10 Kesim (PRS, 50 sn) → 20 Büküm (PRS, 30 sn) → 30 Montaj (MNT, 40 sn).

### E2E-01 Kimlik, master veri, kapasite (`test_e2e_01_auth_master_capacity.py`)
| Senaryo | Doğrulanan |
|---|---|
| Rol matrisi | admin `/auth/me`; yanlış şifre ve tokensiz istek reddi; `user` rolü okur, master/plan/kullanıcı yazamaz (403); pasif kullanıcı giremez |
| Haftalık kapasite | Excel ile iş merkezi/vardiya/haftalık iş gücü/makine → kapasite 200 saat = 20 birim, 5 gün; haftalık istisna (5 kişi → 100 saat), listeleme, Excel dışa aktarım, Excel ile geri yükleme → 200 |
| İş merkezi API CRUD | Oluştur, vardiya/makine ekle, güncelle (planlama dışı), makine ve merkez sil |
| Yardımcı uç noktalar | Ürün grupları, stok kartı arama/detay, kaynak modeli istatistiği, takvim istisnaları, veri bütünlüğü |

### E2E-02 Sipariş → ihtiyaç → plan (`test_e2e_02_orders_planning.py`)
| Senaryo | Doğrulanan |
|---|---|
| Sipariş import ve ihtiyaç | Önizleme yazmaz, Excel önizleme raporu; import sonrası **18.000 adet × 120 sn = 600 saat** (PRS 400 / MNT 200); API CRUD, miktar 0 ve tanımsız stok reddi, kapatma |
| Termine göre otomatik plan | Ön kontrol; plan sonrası PRS 1. hafta **200 saat / %100**, toplam 600 saat, hiçbir hafta kapasiteyi aşmaz; plan satırları, yük detayı, haftalık çıktı, sipariş takvimi, Gantt, ciro, tüm Excel'ler; temizleme |
| Ciro modu / karşılaştırma / değerlendirme | 1 haftalık dar ufukta pahalı sipariş seçilir; `/plan/compare`; `/plan/evaluation` deterministik parmak izi |
| Manuel satır ve tahmin | Manuel satır ekle → 6 saat güncelle → yük tablosunda görünür → sil; terminleme **720 adet = 24 saat / 3 adım** → tahmin kaydı → listele → temizle |
| Rotasız sipariş | Ön kontrol uyarır, eksik rota Excel'i iner, plan **400 ile açıkça durur** (sessiz kayıp yok); sipariş kapatılınca plan çalışır |

### E2E-03 Senaryo matrisi, günlük çizelge, Gantt (`test_e2e_03_scenario_daily_gantt.py`)
| Senaryo | Doğrulanan |
|---|---|
| Geçiş kuralı → terminleme | Akış düğümleri Kesim→Büküm→Montaj; kuralsız ardışık; "5 çevrim sonra" kuralıyla iç içe başlangıç, öncekinden önce bitmez, toplam termin kısalır; Excel ile kural yükleme, listeleme, silme |
| Günlük detaylı çizelge | Makine bazlı rota + 08-12 vardiya → `daily_detailed` plan segment üretir; makine segmentleri çakışmaz; segment kilidi yeniden planda korunur; Gantt çubuğu ve stok filtresi |

### E2E-04 Üretim, duruş, MES, teslimat riski, veri tazeliği (`test_e2e_04_progress_mes_risk.py`)
| Senaryo | Doğrulanan |
|---|---|
| Legacy üretim ve duruş | Pazartesi 2880 adet Kesim = **40 saat gerçekleşme**, beklenen = plan/5, kalan = plan − gerçekleşen; Gantt üretileni gösterir; duruş 3700 dk ölçülen − 3600 beklenen = **100 dk fazla**, en büyük sebep MLZ; çevrim süresi analizi ve Excel'ler |
| MES akışı | Önizleme yazmaz; token'sız import 422; token ile import; **aynı dosya tekrar import mükerrer yaratmaz** (defterde 150 adet tek sefer); tanımsız kod serbest stok; MES ilerleme, stok defteri, teslimat riski (salt okunur) |
| Veri tazeliği | 4 kontrol noktası; "değişiklik yok" onayı; bilinmeyen anahtar reddi |

### E2E-05 Stok, revizyon, parti, yedek, owner (`test_e2e_05_stock_revision_merge_backup.py`)
| Senaryo | Doğrulanan |
|---|---|
| Stok → rezervasyon → sevk | Giriş 60 → manuel 30 + otomatik (termin sırası) 30 → serbest 0; aşan rezervasyon 400; kısmi/tam sevk; Excel ile giriş; sipariş tamamen sevk edilince kapanır; sevk iptali siparişi açar |
| İş taşıma revizyonu | Taşıma önizleme (1800 adet, kilit yok); revizyon → değişiklik → hesapla (kimse ötelenmez, canlı plan değişmez) → onayla → B 3. haftaya taşındı, A yerinde; reddet ve iptal akışları canlı planı etkilemez |
| Parti ve birlikte sevk | Birleştirme önerisi; üretim partisi → plan **1080 adet × 100 sn = 30 saat** → parti çözme; birlikte sevk seçenekli plan, iki poz takvimde |
| Yedek / şablon / günlük | Yedek Excel'de Siparişler + Haftalık İş Gücü sayfaları; tüm import şablonları iner; import günlüğü |
| Owner temizlik | Owner girişi/istatistik; admin 403; kayıt listesi; önizleme; onay kelimesi olmadan ret; `SIL` ile seçili siparişler kalıcı silinir |

### E2E-06 Termin geri çekme stres senaryosu (`test_e2e_06_pullin_stress.py`)
Saha durumu: 55 müşteri / 160 açık sipariş / 40 ürün / 5 iş merkezi planlıyken 5 müşteri aynı gün 40 farklı ürünün terminini 14 gün öne çekmek istiyor.
Test deterministik veri üretir (seed 2026), canlı planı kurar, 40 revize termini tek revizyona toplu girer, hesaplar ve şunları ölçer:

| Adım | Doğrulanan |
|---|---|
| Talep analizi | Önerilen takvimden her talebin **karşılandı / karşılanamadı** durumu; gecikme revize termine göre ölçülüyor |
| Yan etki | Talep dışı siparişlerden bitişi geriye kayanlar ve **yeni geç kalanlar** (canlı vs önerilen takvim farkından) |
| Kısmi kabul | Karşılanamayan talepler taslaktan çıkarılır, ikinci hesapta kabul edilenlerin tamamı hâlâ karşılanır |
| Onay tutarlılığı | Onay sonrası canlı sipariş takvimi = ekranda görülen öneri (birebir); revize terminler siparişe yazılır |
| İş taşıma yolu | Dolu haftaya 4 iş taşındığında kaydırılan işler **adıyla** raporlanır; red canlı planı korur |
| Performans | 40 değişiklik girişi ve hesaplama süreleri |

Çıktı: `outputs/e2e/pullin_stress_report.md` (planlamacı raporu: karşılanan / karşılanamayan / ötelenen tabloları + arayüz bulguları) ve `pullin_stress_data.json`.

## Canlı duman testi notları
* Giriş bilgisi `.env` içindeki ilk admin kaydından okunur; şifre değiştiyse `E2E_USER` / `E2E_PASS` ortam değişkeni verin.
* Yalnızca GET ve saf hesaplama POST'ları (ihtiyaç, ön kontrol, terminleme) çağrılır. Plan yazma, import, MES aktarımı, stok hareketi **yapılmaz**.
* Her kontrol için süre (ms) raporlanır; yavaşlayan uç noktaları izlemek için `live_smoke.json` saklanabilir.

## Bulgular ve çözümler (2026-09-22)
* **Test izolasyonu (çözüldü):** `tests/conftest.py` artık geliştiricinin `.env` dosyasından bağımsız çalışır (`PRODUCTION_SOURCE=legacy` zorlanır; MES modunu sınayan testler monkeypatch kullanır) ve testler arasında bitmiş ürün depo girişlerini de temizler. `test_backup_faz09` dışa aktarımdan önce ORM durumunu tazeler; `test_revenue_modes._setup` paylaşılan UCUZ/PAHALI kartlarının rotasını sıfırlar. Tam paket 363 / 363 geçer.
* **Revizyon ekranı (çözüldü):** karşılaştırma yanıtı `order_diffs` + `diff_summary` (sipariş bazlı önce/sonra, talep karşılandı/karşılanamadı, ötelenen, yeni geç, öne alınan, kaydırılan) taşır; `POST /plan/revisions/{id}/changes/remove` ile karşılanamayan talepler tek işlemle geri çekilir. Ekranda sipariş başına yeni termin tablosu, özet kartları, filtre/arama, CSV indirme ve "geri çek ve yeniden hesapla" aksiyonu vardır. Sözleşme `tests/test_revision_order_diffs.py` ve E2E-06 ile korunur.
* **Canlı veride yavaş uç noktalar (düzeltildi, çıktı hash'i birebir aynı):** veri bütünlüğü 45,5→6,7 sn (BOM döngü kontrolünde mamul başına sorgu kaldırıldı), iş gücü ihtiyacı 29→16 sn (sipariş başına netleme/WIP/plan sorguları toplu yüklenir), haftalık çıktı 8,7→0,6 sn ve Gantt (hafta takvimi/kapasitesi satır başına yeniden hesaplanmaz), yedek Excel akış modunda yazılır (`lxml` ile daha hızlı; içerik hücre hücre doğrulandı, `scripts/verify_backup_equivalence.py`). Kalan maliyet MES kredisi hesabında tüm stok kataloğunun yüklenmesidir (~10 sn; semantik riski nedeniyle dokunulmadı).
* **Fazla mesai (22.09.2026):** haftalık iş gücüne 18:00-21:00 fazla mesai (kişi/gün/saat ≤ 2,5) eklendi; kapasite, Excel, revizyon ve öneri akışı `tests/test_overtime.py` ile korunur. İlke denetimi: `docs/planlama-ilkeleri-denetimi.md`.
* Ölçüm betiği: `backend/scripts/profile_slow_endpoints.py <etiket>` (salt okunur, `outputs/e2e/perf_<etiket>.txt/json`).
* Rotası olmayan açık sipariş varken otomatik plan ve revizyon hesabı durur (400). 22.09.2026'dan itibaren **sipariş giriş kapısı** böyle siparişin girilmesini/aktarılmasını engeller (`tests/test_order_routing_gate.py`); eski kayıtlar için ön kontrol raporu geçerlidir.
* Revizyon yeniden hesabı arka plan işi olarak çalışır (`/plan/revisions/{id}/calculate/jobs`); ekran ilerlemeyi gösterir, 20 sn istemci zaman aşımına takılmaz.
