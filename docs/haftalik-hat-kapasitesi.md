# İstasyon bazında haftalık hat kapasitesi

İş Merkezleri → Düzenle → **Hat kapasitesiyle planla** seçilir. Kişi başı verimli saat pasif olur ve hat kapasitesi hesaplarında kullanılmaz.

**İstasyonlar** sekmesinde her hat için gerekli ekip (pozitif tam sayı) tanımlanır.
**Haftalık hat saatleri** sekmesinde her aktif istasyona haftalık toplam üretime ayrılabilir saat (0–168) girilir.
Boş saat veya ekip kapasite oluşturmaz; 0 saat o hafta çalışılmayacağını belirtir.
Vardiya, mola, tatil, günlük takvim ve eski iş merkezi iş gücü verilerinden saat devralınmaz veya kesinti yapılmaz.

İş merkezi kapasitesi = aktif ve ekip tanımlı istasyonların haftalık saatleri toplamı.
Kişi-saat ihtiyacı = her istasyonda haftalık saat × gerekli ekip toplamı.
Örnek: A 35 saat × 3 kişi, B 20 saat × 2 kişi → 55 hat-saat ve 145 kişi-saat.
Ekip bir ihtiyaç bilgisidir; personel ataması veya aynı kişilerin eşzamanlı kullanılabilirlik kontrolü değildir.

Stok Kodları → Operasyon Detayları → Kaynak bölümünde dizilim adedi, dizilim çıkış aralığı (sn), birincil ve alternatif uygun istasyonlar tanımlanır.
Hat yükü (saat) = [BOM çevrim süresi + (ceil(miktar / dizilim adedi) − 1) × çıkış aralığı] / 3600. Sıfır miktarda yük sıfırdır. Tavlama ve yıkama için çıkış aralığı 5 saniyedir.
İlk grup BOM çevrim süresinde tamamlanır; son eksik grup tam grup sayılır. Ayrı istasyon veya haftaya bölünen her parti ilk grup süresini yeniden kullanır. Ek setup süresi uygulanmaz.
Tekrarlı operasyonlar ayrı yük oluşturur. Ekip artırmak hat hızını veya kapasitesini artırmaz.

Plan her operasyonu yalnızca uygun aktif istasyonlara yerleştirir. İstasyon ve merkez bütçeleri beraber tüketilir;
merkezdeki atıl kapasite oranı her istasyona uygulanır. Uygun alternatif varsa aynı hafta farklı istasyonlara bölünebilir.
Plan satırındaki machine_id ve arayüzdeki istasyon kodu, kullanılan hattı gösterir. Revizyon snapshot'ı atamayı korur.
İstasyon saati, ekip, aktiflik veya operasyonun uygun istasyonları değişirse eski plan önizlemesi geçersiz olur.
Manuel plan tek istasyona sığmalı; otomatik plan ve tahmin uygun istasyonlar arasında bölebilir.

Eski merkez ekip/gün/saat verileri istasyonlara otomatik dağıtılmaz. Mevcut plan kendiliğinden yeniden yerleşmez.
Korunan eski bir hat planında istasyon ataması yoksa o haftada yeni istasyon kapasitesi kullandırılmaz; ön kontrol bunu bildirir.
İstasyon saatleri günlük vardiya zamanlarını içermez. Termin tahminindeki gün/saatler haftayı gösteren yaklaşık tarihlerdir.
Günlük detaylı çizelgeleme bu hatları yeniden iş gücü mantığıyla yerleştirmez (weekly_line_capacity_only); haftalık plan geçerlidir.

Şema: machines.required_crew_size, machine_weeks(machine_id, week_start, working_hours), plan_lines.machine_id.
Eski merkez alanları geriye uyumluluk için saklanır ancak hat hesabında kullanılmaz. Başlangıç şema geçişi yeni alan ve tabloyu ekler.
Diğer iş merkezlerinin iş gücü modeli değişmez.
