# Yedekleme ve geri dönüş

## PostgreSQL (tam veritabanı)

```powershell
Set-Location backend
.\scripts\backup_postgres.ps1
```

- Çıktı: `backend/backup/kapasite_YYYYMMDD_HHmmss.dump` (pg_dump **custom format**, şema + veri)
- Yanında: `.manifest.json` (zaman, boyut, doğrulama durumu)
- **Rol / GRANT** bu dump’ta yok; sunucu erişim kurulumu ayrı yapılmalıdır.
- Yedek dosyaları git’e eklenmez (`backend/backup/`).

### Test ortamına geri yükleme

Yalnızca adında `audit` veya `test` geçen geçici veritabanları (ör. `kapasite_audit_test`). **`kapasite` canlı adı reddedilir.**

```powershell
.\scripts\restore_postgres_test.ps1 -DumpFile ".\backup\kapasite_20260914_120000.dump" -TargetDatabase kapasite_audit_test
```

Restore sonrası `scripts/validate_restore_db.py` kritik tablo sayılarını ve bütünlük denetimini çalıştırır.

## Excel veri dışa aktarımı

UI veya `GET /api/backup.xlsx` — **Excel veri dışa aktarımı**; eksiksiz DB geri dönüşü değildir.

- Import şablonlarıyla uyumlu alanlar (BOM dal/sıra, planlama rezervi, rota birincil makine, rota istasyonları, …)
- Partiler / revizyonlar **referans** sayfalar; iç kimliklerle otomatik geri yükleme vaat edilmez.

## Veri bütünlüğü API

`GET /api/data-integrity` — uyarı/hata listesi (sessiz silme yok).
