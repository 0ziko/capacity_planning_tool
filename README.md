# Bilge İnox — Sipariş İş Gücü İhtiyaçlarına Göre Üretim Kapasitesi Planlama

İş merkezi bazlı **verimli iş gücü kapasitesi** ile sipariş kaynaklı **iş gücü ihtiyacını** karşılaştıran, günlük üretim/duruş importuyla ilerlemeyi izleyen web uygulaması. Detaylı gereksinim ve tasarım notları `memory-bank/` altında.

## Yapı
- `backend/` — Python 3.12, FastAPI, SQLAlchemy 2, openpyxl. PostgreSQL hedef; yerelde SQLite ile de çalışır.
- `frontend/` — React 18 + TypeScript + Vite (Türkçe arayüz).
- `memory-bank/` — proje hafızası (gereksinimler, mimari, ilerleme).

## Çalıştırma (yerel)
```powershell
# Backend (http://localhost:8000, API dokümanı /docs)
cd backend
.\scripts\setup_postgres.ps1   # ilk kez: PostgreSQL + SQLite veri tasima + .env
.\run_dev.ps1                  # venv kurar, uvicorn baslatir

# Frontend (http://localhost:5173) — ayrı terminal
cd frontend
npm install
npm run dev
```
İlk giriş: `admin / admin123` (`.env` → `FIRST_ADMIN_*` ile değiştirin).

## PostgreSQL

Uygulama PostgreSQL ile calisacak sekilde hazir (`psycopg` zaten requirements'ta). Yerel kurulum:

**Portable (admin gerektirmez — önerilen):**
```powershell
cd backend
.\scripts\install_portable_postgres.ps1
.\run_dev.ps1
```

**Docker:**
```powershell
docker compose up -d
cd backend
.\scripts\setup_postgres.ps1   # portable'a yonlendirir; once start_postgres.ps1
```
Script mevcut `kapasite_dev.db` (SQLite) verisini PostgreSQL'e tasir ve `.env` icinde `DATABASE_URL` gunceller.

Manuel veritabani olusturma (psql):
```sql
CREATE USER kapasite WITH PASSWORD 'kapasite';
CREATE DATABASE kapasite OWNER kapasite;
```

Canli ortamda guclu sifre ve `SECRET_KEY` kullanin. SQLite yalnizca gecici/yedek gelistirme icin birakildi; testler otomatik SQLite kullanir.

## Test
```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```

## Kullanım akışı
1. **Excel Import**: İş merkezleri → Vardiyalar → Personel → Stok → BOM → Rota → Siparişler (her biri için şablon indirilebilir).
2. **İş Merkezleri**: kişi başı verimli saat, vardiya, “1 birim = N saat”, pilot için **Planlanıyor** işareti.
3. **Planlama**: Otomatik (termine göre) veya manuel; haftalık doluluk; Excel'e aktarım; yeni iş terminleme.
4. Her gün **Günlük Üretim** ve **Duruş** importu → **Günlük İlerleme** ve **Duruş Analizi**.
5. Veri biriktikçe **Çevrim Süresi Önerileri** (Excel raporu).
6. **Tek tıkla Excel yedeği**: tüm tablolar, şablonlarla uyumlu.
