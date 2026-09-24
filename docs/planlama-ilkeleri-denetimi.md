# Planlama Senaryosu Çalışma Şekli — İlke Denetimi (22.09.2026)

Kullanıcının beş ilkesi mevcut motorla kod ve test kanıtı üzerinden karşılaştırıldı. "Durum" sütunu: ✅ karşılanıyor · ⚠️ kısmen · ❌ eksik.

| # | İlke | Durum | Kanıt / uygulama |
|---|---|---|---|
| 1 | Tümdengelim: istenen sevk tarihinden geriye operasyon başlangıcı | ⚠️ | Motor termini esas alır: adaylar **etkin termine göre sıralanır** ve her operasyon öncül/geçiş kuralına göre **en erken uygun haftaya** yerleşir (`planning.simulate` → `_place_quantity`, `opcon.resolve_min_start_for_op`). Termin kapasite izin verdiği sürece tutulur; tutulmuyorsa sipariş "geç" ya da "plansız" olarak açıkça raporlanır (sipariş takvimi, ön kontrol). Ancak yerleştirme **ileriye doğru (ASAP)** yapılır; "termine göre en geç başlangıç" (ALAP/JIT) yerleştirme yoktur. Terminleme ekranı (`/plan/leadtime`) yalnızca ileriye doğru hesaplar. |
| 2 | Kurulu kapasitenin etkin kullanımı; atıl kapasite planlamacı yönlendirmesiz kalmasın | ⚠️ | Otomatik plan kapasiteyi en erken haftalardan itibaren doldurur; yük tablosu her hafta için **atıl saati** gösterir (`idle_hours`), fazla mesai payı ayrıca (`overtime_hours`). Planlamacı yönlendirmesi için araçlar: iş taşıma revizyonu (öne çekme), termin revizyonu, ciro modu, manuel satır. Eksik: atıl haftaya "öne çekilebilecek işler" önerisi otomatik üretilmiyor. |
| 3 | Ara stok minimum; atıl kapasitede ihtiyaç duyulacak yarımamül üretilebilir | ⚠️ | Operasyonlar arası geçiş kuralları (önceki bitince / N çevrim sonra iç içe) ve günlük çizelgede öncül-ardıl miktar bağı ara stoğu sınırlar; MES stok defteri yarımamül bakiyesini izler. Ancak ASAP yerleştirme, kapasite bolsa erken operasyonları terminden çok önce çalıştırıp ara stok yaratabilir. JIT yerleştirme seçeneği bunu azaltır; ilke 2 ile çelişkiyi planlamacı kararına bırakmak gerekir (bkz. öneri A). |
| 4 | İş merkezi iş gücü kısıtı her zaman dikkate alınsın (haftalık verimli iş gücü) | ✅ | Kapasite = haftalık kişi × kişi başı verimli saat × çalışma günü (`capacity.daily_capacity_hours`, `WorkCenterWeek`); planlanabilir kapasite atıl rezerv düşülerek hesaplanır; **eksik haftalık kişi girişi planı durdurur** (ön kontrol, açık sıfır onayı). Fazla mesai bu kısıtın içinde: kişi sayısı haftanın kişi sayısını aşamaz. Testler: `test_weekly_labor_contract.py`, `test_planning_capacity.py`, `test_plan_preflight.py`, `test_overtime.py`. |
| 5 | Darboğazda fazla mesai önerisi; 18:00-21:00, kişi başı 2,5 sa; onaylı ve kısıtlı | ✅ | **Uygulandı** (aşağıda). |

## 5. ilke: fazla mesai altyapısı (uygulandı)

- **Veri modeli:** `WorkCenterWeek.overtime_headcount / overtime_days / overtime_hours_per_person` (boş = fazla mesai yok; saat üst sınırı 2,5). Tek kaynak: haftalık iş gücü kaydı. Mevcut kayıtlar etkilenmez (yeni sütunlar boş).
- **Kapasite formülü:** normal `kişi × verimli saat` + fazla mesai `FM kişi × 2,5 sa × (verimli saat / vardiya nominal saati) × FM gün`. Verim oranı vardiyanınkiyle aynı varsayılır (ör. 4 / 10 = 0,40). Nominal adam-saat (duruş beklentisi) de fazla mesai kadar artar.
- **Kısıtlar (API, Excel, revizyon):** FM kişi ≤ haftanın kişi sayısı; FM gün ≤ çalışma günü; saat ≤ 2,5. Dizilim (hat) modunda fazla mesai istasyon saatleriyle tanımlanır (öneri üretilmez).
- **Nerede görünür:** Haftalık iş gücü ekranı (FM kişi/gün/saat sütunları, kapasite "+FM" ayrımı), planlama yük tablosu ("FM +x sa"), Excel haftalık iş gücü şablonu/dışa aktarımı/yedeği (3 yeni sütun), günlük ayrıntılı çizelge (18:00'dan itibaren fazla mesai penceresi makine aralıklarına eklenir).
- **Onaylı akış (kontrollü yük):** Plan revizyonunda "İş gücü ekle" fazla mesai kişi/gün alır; hesaplanmış revizyonda **Fazla mesai önerisi** kutusu, kapasite yetersizliğinden plansız kalan saati iş merkezi bazında toplar ve dolu haftalara kişi sayısı önerir (kişi başı hafta katkısı = 2,5 × verim oranı × gün). "Taslağa ekle" → yeniden hesap → **onayla** ile haftalık iş gücüne yazılır; canlı veri onaya kadar değişmez. Kısıt dışı taslak hesapta reddedilir.
- **API:** `GET /plan/revisions/{id}/overtime-suggestions`; wc_week değişiklik alanları `overtime_headcount`, `overtime_days`, `overtime_hours_per_person`.
- **Testler:** `backend/tests/test_overtime.py` (kapasite 200 → 212 örneği, kısıt redleri, Excel gidiş-dönüş, öneri → taslak → onay, günlük pencere).

## Uygulanan öneriler (22.09.2026, kullanıcı onayıyla)

- **A. JIT (termine yakın) yerleştirme:** otomatik plan ve revizyonda "Yerleştirme: En erken (ASAP) / Termine yakın (JIT)" ve "Tampon (gün)" (varsayılan 2). JIT, mevcut ASAP sonucunu son geçişte yalnızca daha geç haftalara kaydırır; kapasite yoksa iş erken kalır ve sipariş takvimindeki **"Termine kalan (gün)"** sütunu (etkin termin − tahmini bitiş) erken üretimi gösterir (>14 gün turuncu, <0 kırmızı). Planlamacı atıl kapasiteyi kullanmayı seçerse (B) bu farkı görerek karar verir.
- **B. Atıl kapasite önerisi:** yük tablosunda "Atıl: X sa ▸" tıklanınca öne çekilebilir işler penceresi; öncülü hazır ve malzemesi uygun olanlar termin sırasıyla, atıl saate sığanlar önceden seçili; tek tıkla iş taşıma revizyon taslağı (hesapla → onayla).
- **C. Ara stok sınırı:** yarımamül kartında azami adet ve/veya gün (Stok Kodları ekranı ve Excel'i). Plan son geçişi öncülü ardıla yaklaştırır; kapasite yoksa açık uyarı üretir. Günlük ayrıntılı çizelgeye henüz doğrudan yansımaz.

- **D. Akış modu (23.09.2026):** "Yerleştirme: Akış (maks. çıktı, min. ara stok)". Bitiş operasyonu en erken (maksimum bitmiş ürün), öncüller ardılın haftasına yaslanır (ara stok minimum), boşalan erken kapasite sonraki siparişlere açılır (toplam kapasite kullanımı). Kapasite izin vermezse öncül yerinde kalır; notlarda görünür.
- **E. Tüm ufuk öne çekme (23.09.2026):** yük tablosu üstünde tek düğme: ufuktaki tüm atıl haftalar taranır, uygun işler sipariş başına bir kez en erken sığdığı haftaya çekilir, tek revizyon taslağı oluşur.

## Önceki öneri metni (referans)

- **A. JIT (termine yakın) yerleştirme modu (ilke 1 ve 3):** otomatik plana `placement = asap | jit` seçeneği; JIT modunda son operasyon termin haftasından geriye, öncüller ardılın haftasına göre geriye yerleşir. Varsayılan ASAP kalır (mevcut davranış korunur). Etkileri: ara stok ve erken bitmiş ürün azalır; erken haftalarda atıl kapasite artar (ilke 2), bunu planlamacı öne çekme/fazla mesai ile yönlendirir. Geçiş kuralları, partiler, birlikte sevk ve günlük çizelgeyle etkileşim nedeniyle ayrı bir faz olarak ele alınmalı.
- **B. Atıl kapasite önerisi (ilke 2):** yük tablosunda atıl haftalar için "öne çekilebilir işler" (sonraki haftalarda planlı, öncülü hazır, malzemesi hazır siparişler) listesi ve tek tıkla iş taşıma taslağı.
- **C. Ara stok hedefi (ilke 3):** yarımamül kodu bazında azami ara stok (adet/gün) parametresi; plan ve günlük çizelge bu sınırı aşan öncül üretimini geciktirir.
