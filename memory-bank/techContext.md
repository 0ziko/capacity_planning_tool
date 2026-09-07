# Teknik Bağlam

## Karar Verilen Yığın (2026-09-07, kullanıcı onayı)
- **Backend:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · Pydantic v2 · openpyxl · PyJWT · bcrypt · uvicorn
- **Veri tabanı:** **PostgreSQL** (hedef, `psycopg` sürücüsü). Yerel geliştirmede `DATABASE_URL` verilmezse **SQLite** (`kapasite_dev.db`) ile çalışır; kod dialect-bağımsız (yalnızca ORM).
- **Frontend:** **React 18 + TypeScript + Vite** SPA, `react-router-dom`. UI kütüphanesi yok; `styles.css` ile sade tablo odaklı arayüz. Türkçe.
- **Dağıtım:** Önce localhost (kullanıcı PC), sonra şirket içi sunucuya taşınacak.
- **Şema yönetimi:** Şimdilik `Base.metadata.create_all` (ilk açılış). Şema değişikliği gerekirse Alembic eklenecek.

## Sabit Kısıtlar (spec'ten)
- Web tabanlı, çok kullanıcılı; roller admin / poweruser / user.
- Ücretsiz SQL veri tabanı.
- Excel import/export her modülde zorunlu (.xlsx).
- Windows ortamı (kullanıcı makinesi Windows 11, PowerShell, admin yetkisi YOK).

## Geliştirme Ortamı (bu makinede kurulu)
- Python 3.12.10 → `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` (winget, user scope)
- Node v24 LTS (portable) → `%LOCALAPPDATA%\Programs\nodejs` (kullanıcı PATH'ine eklendi; yeni terminallerde geçerli)
- Git 2.55 → `%LOCALAPPDATA%\Programs\Git\cmd` (winget, user scope)
- PostgreSQL **kurulu değil** (kurulum admin gerektirir). Yerel geliştirme SQLite ile yapılıyor.
- Proje kökü: `C:\Users\ozan.deniz\Desktop\BilgeInox-KapasitePlanlama\`

## Çalıştırma
```powershell
cd backend;  .\run_dev.ps1            # venv + pip + .env + uvicorn :8000  (API dokümanı /docs)
cd frontend; npm install; npm run dev # :5173, /api -> :8000 proxy (vite.config.ts)
# Backend'i arka planda, log dosyasina yazarak baslatmak icin (tercih edilen):
cd backend; .\start_backend.ps1        # durdurmak: .\start_backend.ps1 -Stop ; loglar: backend\logs\backend.err.log
# Not: uvicorn'u bir arac kabugu altinda `2>&1` ile calistirma — stderr borusu tikaninca ilk traceback tum sunucuyu kilitler (07.09 olayi).
cd backend;  .\.venv\Scripts\python.exe -m pytest -q     # testler
cd backend;  .\.venv\Scripts\python.exe seed_demo.py     # demo veri (API üzerinden Excel import akışı)
```
İlk admin: `.env` → `FIRST_ADMIN_USERNAME/PASSWORD` (varsayılan admin / admin123 — üretimde değiştir).

## Bağımlılıklar
- `backend/requirements.txt`: fastapi, uvicorn[standard], sqlalchemy, psycopg[binary], pydantic, pydantic-settings, PyJWT, bcrypt, python-multipart, openpyxl, httpx, pytest
- `frontend/package.json`: react, react-dom, react-router-dom; dev: vite, @vitejs/plugin-react, typescript, @types/react*

## Araç Notları (Cursor / bu makine)
- Cursor sandbox shell komutlarını çalıştıramıyor → komutlar `all` izniyle çalıştırılıyor.
- Cursor Write aracı bu makinede bazı dosyaları BOM'suz **UTF-16LE** yazdı (özellikle memory-bank ilk oluşturma). Read/Glob bunları "binary" sayıyor. Kontrol: dosyanın 2. baytı `0x00` ise UTF-16. Dönüştürme (PowerShell):
  `[IO.File]::WriteAllText($p, [Text.Encoding]::Unicode.GetString([IO.File]::ReadAllBytes($p)), (New-Object Text.UTF8Encoding($false)))`
  Kod dosyaları (backend/frontend) UTF-8 olarak yazıldı; yeni dosya yazımından sonra encoding kontrolü yapılmalı.
- Shell çıktısında görülen `Add-Content : Akış okunabilir değildi` hataları Cursor'un kendi log sarmalayıcısından geliyor; komut sonucunu etkilemiyor.
