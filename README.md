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
.\run_dev.ps1          # venv kurar, bağımlılıkları yükler, .env oluşturur, uvicorn başlatır

# Frontend (http://localhost:5173) — ayrı terminal
cd frontend
npm install
npm run dev
```
İlk giriş: `admin / admin123` (`.env` → `FIRST_ADMIN_*` ile değiştirin).

PostgreSQL için `backend/.env` içinde `DATABASE_URL=postgresql+psycopg://kullanici:sifre@sunucu:5432/kapasite`.

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
