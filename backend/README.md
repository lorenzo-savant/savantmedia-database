# Savantsdatabas backend

Backend Python per la pipeline dati di Savantsdatabas. Allo stato attuale
contiene **solo T0 — Open Data import** (bulk Bolagsverket / SCB). Tutto il
resto (FastAPI orchestrator, LangGraph, scraper tiers, agente RAG) verrà
costruito in fasi successive sopra queste fondamenta.

Tutto qui dentro è **costo zero**: usa dati pubblici CC-BY-4.0 e gira sulla
tua macchina dev.

---

## Stato

| Componente | Stato |
|---|---|
| Importer bulk Bolagsverket / SCB | ✅ MVP funzionante |
| FastAPI scaffold (`/health`, ecc.) | ⏳ Prossimo step |
| Job queue + Redis | ⏳ |
| LangGraph orchestrator | ⏳ |
| Scraper Tier 1–5 (SearXNG, crawl4ai, Playwright stealth…) | ⏳ |
| Critic node (ispirato AutoGen 0.2) | ⏳ |

Vedi [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) per il quadro completo.

---

## Setup (Windows / PowerShell)

```powershell
# Dentro la cartella backend/
cd backend

# Crea venv (Python 3.12)
python -m venv .venv

# Attiva venv
.\.venv\Scripts\Activate.ps1
# (Se vedi "execution policy" error, esegui una volta:
#  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser)

# Aggiorna pip + installa deps
python -m pip install --upgrade pip
pip install -r requirements.txt

# Crea backend/.env partendo dal template
copy .env.example .env
# Apri .env e popola SUPABASE_URL + SUPABASE_SECRET_KEY
# (gli stessi valori che hai in ../.env.local — Next.js)
```

---

## T0 Open Data — importer Bolagsverket / SCB

Lo script vive in [scripts/import_bolagsverket_bulk.py](scripts/import_bolagsverket_bulk.py).
Si esegue come modulo Python (`python -m scripts.import_bolagsverket_bulk`).

### Comandi

```powershell
# 1. ISPEZIONE — scarica e mostra schema + prime righe
#    (NON scrive nulla nel DB; usalo per capire il formato)
python -m scripts.import_bolagsverket_bulk inspect
python -m scripts.import_bolagsverket_bulk inspect --source scb

# Forza re-download (default: file in cache `./data/bulk/`)
python -m scripts.import_bolagsverket_bulk inspect --force

# 2. APPLY — inserisce in Supabase con un filtro obbligatorio
#    (no filter = aborto, per non saturare il free tier 500 MB)

# Esempio: prime 500 aziende dello Stockholms län
python -m scripts.import_bolagsverket_bulk apply --region "Stockholms län" --limit 500

# Esempio: una sola azienda per testare end-to-end
python -m scripts.import_bolagsverket_bulk apply --orgnr 556677-1234

# --dry-run: mostra cosa scriverebbe, ma non tocca il DB
python -m scripts.import_bolagsverket_bulk apply --region "Skåne län" --limit 10 --dry-run

# 3. STATS — conta quante righe sono già state importate da bulk
python -m scripts.import_bolagsverket_bulk stats
```

### Workflow consigliato la prima volta

1. **Esegui `inspect`** — il file bulk reale potrebbe avere nomi di colonne
   leggermente diversi da quelli che ho assunto. Lo script stamperà lo schema
   auto-rilevato (DuckDB).
2. **Confronta lo schema con `map_row_to_company()`** in
   [scripts/import_bolagsverket_bulk.py](scripts/import_bolagsverket_bulk.py) —
   se servono aggiustamenti (es. la colonna è `lan_namn` invece di `lan`),
   aggiorna `_get(row, ...)` con i nomi reali. Le costanti `ORGNR_COLUMN` e
   `REGION_COLUMN` in alto vanno cambiate solo se il filtro WHERE deve usare
   nomi diversi.
3. **Esegui `apply --dry-run`** con un filtro stretto per vedere cosa
   verrebbe scritto.
4. **Esegui `apply`** vero quando ti convince.

### Audit trail

Ogni record importato lascia una traccia nella tabella `sources`:
- `field_name = "companies.*"`
- `source_url = <URL bulk>`
- `scraper_tier = 0` (T0 = open data)
- `raw_excerpt = "bolagsverket_bulk"` (tag per `stats`)
- `license_label = "CC-BY-4.0"` (attribuzione obbligatoria)

Questo è il "perché ci fidiamo di questo dato" che la UI dei colleghi può
mostrare.

---

## Cache locale

I file bulk scaricati vivono in `backend/data/bulk/` (gitignored).
Non vengono re-scaricati a meno che non passi `--force`.

---

## Costi (memo)

- Bolagsverket bulk: **gratis** (CC-BY-4.0, Direttiva UE HVD dal feb 2025)
- SCB bulk: **gratis** (stessa direttiva)
- Supabase free tier: 500 MB DB. Filtra sempre prima di importare.
- LLM (Ollama): locale, **gratis**, non usato in T0.
- Networking: solo download iniziale (~centinaia di MB).

---

## Cosa NON fa ancora

- Niente scraping. Per dati che il bulk non copre (categorie bransch
  dettagliate, fatturato, contatti email, LinkedIn) servirà la pipeline
  agentica Tier 1–5 + critic, che vivrà accanto a questo importer.
- Niente FastAPI server attivo. L'importer è uno script CLI standalone.
- Niente UI di controllo. Tutto da terminale al momento.

Questi sono i passi successivi della roadmap.
