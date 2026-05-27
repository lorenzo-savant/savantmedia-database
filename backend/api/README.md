# Savantsdatabas Orchestrator API

FastAPI app che espone health, stats e (futuro) orchestrazione scraping.
Riferimento design: `docs/ARCHITECTURE.md` — Fase 5.

## Run (dev)

Dalla root del repo:

```powershell
cd backend
.venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000
```

Variabili d'ambiente lette da `backend/.env`:

- `SUPABASE_URL` — project URL
- `SUPABASE_SECRET_KEY` — service role (nuova convenzione)
- `ORCHESTRATOR_API_TOKEN` *(opzionale)* — se settato, tutte le route protette
  richiedono `Authorization: Bearer <token>`. Se assente, log warning a startup
  e nessuna auth (solo dev locale).
- `LOG_LEVEL` *(opzionale, default `INFO`)*

## Endpoint

| Method | Path        | Auth | Descrizione                              |
| ------ | ----------- | ---- | ---------------------------------------- |
| GET    | `/`         | no   | Liveness probe                           |
| GET    | `/health`   | si\* | Ping Supabase + count companies          |
| GET    | `/db/stats` | si\* | Conteggi companies/contacts/sources/...  |

\* Auth applicata solo se `ORCHESTRATOR_API_TOKEN` e' settato.

### Esempi

PowerShell:

```powershell
# Liveness
Invoke-RestMethod http://localhost:8000/

# Health (no auth)
Invoke-RestMethod http://localhost:8000/health

# Health (con token)
$h = @{ Authorization = "Bearer $env:ORCHESTRATOR_API_TOKEN" }
Invoke-RestMethod http://localhost:8000/health -Headers $h

# Stats
Invoke-RestMethod http://localhost:8000/db/stats -Headers $h
```

curl:

```bash
curl http://localhost:8000/
curl -H "Authorization: Bearer $ORCHESTRATOR_API_TOKEN" http://localhost:8000/health
curl -H "Authorization: Bearer $ORCHESTRATOR_API_TOKEN" http://localhost:8000/db/stats
```

### Esempio risposta `/db/stats`

```json
{
  "companies":        { "total": 0, "arkiverade": 0 },
  "contacts":         { "total": 0, "verified": 0 },
  "sources":          { "total": 0 },
  "plans":            { "total": 0 },
  "scrape_jobs":      { "total": 0 },
  "knowledge_chunks": { "total": 0 }
}
```

## CORS

Whitelistato `http://localhost:3000` per il frontend Next.js in dev.
Quando andra' in produzione, aggiungere il dominio del frontend in
`api/main.py`.

## Next steps (placeholder, non ancora implementati)

- `POST /orchestrator/plan` — crea un piano LangGraph
- `GET  /orchestrator/plan/{id}` — stato del piano
- `POST /scrape/job` — enqueue scrape job
- `GET  /scrape/job/{id}` — stato job
- `POST /knowledge/ingest` — pipeline di ingest

Tutti questi finiranno dietro `verify_token` e si appoggeranno a
`scrape_jobs` / `plans` / `knowledge_chunks`.
