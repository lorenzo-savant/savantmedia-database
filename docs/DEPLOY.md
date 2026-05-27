# Savantsdatabas — Deploy guide (Fase 17)

> Roadmap reference: `docs/ARCHITECTURE.md` § Fase 17 "Deploy prod".
> Quick-start scegli-il-tuo-scenario: `deploy/README.md`.

Guida completa per portare Savantsdatabas in produzione. Pensata per uso interno
Savant Media: pochi utenti (5-15), costi quasi-zero, infrastruttura ricostruibile
in mezza giornata.

---

## 1. Overview architettura deploy

Tre piani logici, ognuno indipendentemente sostituibile:

```
┌─────────────────────┐     HTTPS      ┌────────────────────────┐
│   FRONTEND          │ ─────────────▶ │   SUPABASE managed     │
│   Next.js 15        │ ◀───────────── │   Postgres + Auth +    │
│   Vercel free tier  │   Realtime WS  │   Realtime + pgvector  │
│   db.savantmedia.se │                │   tttcbwquiexahcmcuech │
└──────────┬──────────┘                └────────────┬───────────┘
           │                                        ▲
           │ HTTPS                                  │ Service key
           │ + ORCHESTRATOR_API_TOKEN               │ (server-side)
           ▼                                        │
┌─────────────────────┐                             │
│   BACKEND           │ ────────────────────────────┘
│   FastAPI + agent   │
│   VPS Hetzner CX22  │            ┌─────────────────────────────┐
│   o Mac Lorenzo via │  internal  │  Self-hosted (opzionali)    │
│   Tailscale         │ ────────▶  │  • SearXNG    :8888         │
│   api.…savantmedia  │            │  • Redis      :6379         │
└─────────────────────┘            │  • Ollama     :11434        │
                                   └─────────────────────────────┘
```

### Cosa va dove (e perché)

| Componente | Host | Motivazione |
|---|---|---|
| Next.js UI | **Vercel free** | Build automatica da git, CDN globale, dominio custom free, zero ops. |
| Supabase Postgres | **Supabase managed (free)** | Backup giornaliero, Realtime, pgvector pronto. Free tier basta a lungo. |
| FastAPI orchestrator | **VPS Hetzner CX22** o Mac Lorenzo + Tailscale | Long-running scraping (T4/T5 Playwright) e gestione di un agente persistente non vanno bene su edge serverless. |
| SearXNG | docker compose accanto al backend | Search engine privato, niente API limit, zero pagamento. |
| Redis | docker compose accanto al backend | Job queue futura (Fase 6+). |
| Ollama | locale Lorenzo (Mac/PC con GPU) | Modello reasoning + embeddings, free. CPU-only è lento — meglio sulla macchina dev. |

### Costi mensili stimati

| Voce | EUR/mese | Note |
|---|---|---|
| Supabase free | 0 | Limit: 500 MB DB, 2 GB egress, 50k MAU — ampiamente sufficiente per il team. |
| Vercel free (Hobby) | 0 | 100 GB bandwidth, 6000 build-min. Per uso interno è infinito. |
| Hetzner CX22 (Helsinki) | ~5 | 2 vCPU / 4 GB / 40 GB SSD / 20 TB traffic. Tier base. |
| Tailscale free | 0 | Fino a 100 device, abbastanza per Savant Media + clienti pilot. |
| Dominio `savantmedia.se` | (già posseduto) | Si crea sottodominio `db.savantmedia.se` + `api.savantsdatabas.savantmedia.se`. |
| **Totale** | **~5 EUR/mese** | Con backend sul Mac di Lorenzo via Tailscale: **0 EUR**. |

---

## 2. Prerequisiti

### Account (gratis)

- **Supabase** — già creato (`tttcbwquiexahcmcuech`).
- **Vercel** — login con GitHub raccomandato (link al repo).
- **Hetzner Cloud** — solo se vuoi VPS dedicato (alternative: OVH, DigitalOcean, una vecchia macchina + Tailscale).
- **Tailscale** — opzionale, ma molto comodo: collega Mac/VPS/colleghi in una mesh privata senza configurare firewall.

### Tools locali

```bash
# Verifica versioni:
node --version       # >= 20
python --version     # >= 3.12
docker --version     # qualunque versione recente
git --version
supabase --version   # Supabase CLI: npm i -g supabase
vercel --version     # Vercel CLI: npm i -g vercel
```

Su Windows tieni in mente che PowerShell è la shell di default; sostituisci
`.venv/bin/...` con `.venv/Scripts/...` quando vedi snippet Unix.

---

## 3. Step 1 — Supabase prod

### 3.1 Reset password DB

Va fatto una volta sola, **subito**: la password attuale è quella generata al
provisioning ed è probabilmente in qualche history.

1. Vai su https://supabase.com/dashboard/project/tttcbwquiexahcmcuech
2. **Settings → Database → Database password → Reset database password**
3. Salva la nuova password in 1Password / Bitwarden / vault locale.
4. Aggiorna `DATABASE_URL` ovunque la usi (non è obbligatoria per l'app: il
   client Supabase usa publishable/secret key, non la password Postgres).

### 3.2 Applicare le migration pendenti

Sul tuo Windows dev box, dalla repo root:

```powershell
supabase link --project-ref tttcbwquiexahcmcuech
supabase db push
```

Le migration in `supabase/migrations/`:

- `20260527120000_initial_schema.sql` — già applicata.
- `20260527140000_enable_public_grants.sql` — **da applicare entro 2026-10-30**
  (deadline Supabase per il nuovo modello di GRANT su `public`).
- `20260527150000_enable_rls_phase4.sql` — abilita Row Level Security su
  `companies`, `enrichment_jobs`, `pending_claims`, `knowledge_chunks`,
  `timeline_events`.

> **Importante**: senza migration RLS la tabella è leggibile/scrivibile via
> publishable key a tutti gli utenti autenticati. Con RLS attiva, le scritture
> passano solo via service key (server-side route handler + backend).

### 3.3 Configurare Auth

1. **Authentication → Providers → Email** → abilita "Magic Link" (no password).
2. **Authentication → Settings → Site URL** → metti l'URL Vercel finale
   (es. `https://db.savantmedia.se`).
3. **Authentication → URL Configuration → Redirect URLs** → aggiungi:
   - `https://db.savantmedia.se/**`
   - `http://localhost:3000/**` (per dev locale)
4. (Opzionale ma raccomandato) **Email domain allow-list**: limita signup a
   `@savantmedia.se` tramite hook o policy. Dashboard non offre nativo: usa
   una RLS policy su `auth.users` o un trigger custom.

### 3.4 Nuove API keys

Dal **Settings → API Keys** copia:

- `Publishable key` (nuova nomenclatura, sostituisce `anon`)
- `Secret key` (sostituisce `service_role`)

Metti in:

- `.env.local` (dev locale)
- Vercel env vars (sezione 4a)
- `/etc/savantsdatabas/backend.env` (VPS, sezione 5b) — solo `SUPABASE_SECRET_KEY`

### 3.5 Realtime su `companies`

1. **Database → Replication** → abilita pubblicazione su tabella `companies`.
2. Il client già usa `supabase.channel('companies').on('postgres_changes', ...)`.

### 3.6 Backup

- Supabase free: backup giornalieri automatici, retention 7 giorni.
- Vai su **Database → Backups** per verificare. Niente da configurare.
- Per backup manuali extra vedi sezione 9.

---

## 4. Step 2 — Frontend Next.js

### 4a. Deploy su Vercel (consigliato)

```bash
# Dalla repo root, login una volta:
vercel login

# Link al progetto Vercel (crea uno nuovo o seleziona esistente):
vercel link

# Aggiungi env vars (per "production"; ripeti per "preview" se vuoi):
vercel env add NEXT_PUBLIC_SUPABASE_URL              production
vercel env add NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY  production
vercel env add SUPABASE_SECRET_KEY                   production  # encrypted, server-only
vercel env add NEXT_PUBLIC_ORCHESTRATOR_URL          production  # es. https://api.savantsdatabas.savantmedia.se
vercel env add ORCHESTRATOR_API_TOKEN                production  # encrypted

# Deploy:
vercel deploy --prod
```

#### Dominio custom

1. Su Vercel dashboard → Project → Settings → Domains.
2. Aggiungi `db.savantmedia.se`.
3. Vercel ti dà un record CNAME → aggiungilo dal registrar di `savantmedia.se`.
4. Aspetta ~5 minuti, HTTPS automatico via Let's Encrypt.

#### CI/CD

Una volta linkato il repo GitHub, ogni push su `main` triggera deploy prod;
ogni push su altri branch crea un preview env con URL univoco.

### 4b. Alternativa: self-host con Docker

Per evitare Vercel (es. data residency UE strict, o paranoia vendor-lock):

```bash
# Build:
docker build -f Dockerfile.frontend -t savantsdatabas-frontend .

# Run (sostituisci con valori reali; per env locali usa --env-file):
docker run -d --name savant-frontend -p 3000:3000 \
  -e NEXT_PUBLIC_SUPABASE_URL="https://tttcbwquiexahcmcuech.supabase.co" \
  -e NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY="sb_publishable_..." \
  -e SUPABASE_SECRET_KEY="sb_secret_..." \
  -e NEXT_PUBLIC_ORCHESTRATOR_URL="https://api.savantsdatabas.savantmedia.se" \
  -e ORCHESTRATOR_API_TOKEN="<64-hex>" \
  savantsdatabas-frontend
```

Dietro Nginx con HTTPS: lo schema è lo stesso del backend (vedi sezione 5b);
basta puntare `proxy_pass http://127.0.0.1:3000;`.

Per full-stack locale: `docker compose up -d --build` (vedi `docker-compose.yml`).

---

## 5. Step 3 — Backend Python

### 5a. Locale Lorenzo (sviluppo + scraping ops)

Lo scenario "iniziale": il backend gira sulla macchina di Lorenzo, accessibile
ai colleghi via Tailscale.

```powershell
cd C:\Users\loren\Desktop\dev-projects\savantmedia-database\backend

# Una volta sola: crea venv e installa deps.
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium

# Avvio (host 0.0.0.0 = accessibile a tutta la LAN/Tailscale)
.venv\Scripts\python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

#### Esposizione via Tailscale (gratis)

1. Installa Tailscale sul Mac di Lorenzo e sui device dei colleghi.
2. `tailscale up` su tutti.
3. Trova l'IP Tailscale del Mac: `tailscale ip -4` (es. `100.x.y.z`).
4. Dai colleghi (e nelle env vars Vercel) usa `NEXT_PUBLIC_ORCHESTRATOR_URL=http://100.x.y.z:8000`.

> **Caveat HTTPS via Tailscale**: Vercel chiama solo HTTPS in server-side route
> handlers? In realtà Next.js consente `http://` se l'origin è esplicito. In
> caso di problemi: usa Tailscale MagicDNS + Tailscale Funnel per HTTPS gratis,
> oppure metti il backend dietro Nginx con cert Let's Encrypt comunque.

#### Auto-start su Windows (NSSM)

```powershell
# Scarica NSSM da https://nssm.cc/, poi:
nssm install savantsdatabas-backend
# Path: C:\Users\loren\Desktop\dev-projects\savantmedia-database\backend\.venv\Scripts\python.exe
# Arguments: -m uvicorn api.main:app --host 0.0.0.0 --port 8000
# Startup directory: C:\Users\loren\Desktop\dev-projects\savantmedia-database\backend
nssm start savantsdatabas-backend
```

#### Alternativa cross-platform: pm2

```bash
npm i -g pm2
pm2 start ".venv/Scripts/python.exe" --name savant-backend -- \
    -m uvicorn api.main:app --host 0.0.0.0 --port 8000
pm2 save
pm2 startup   # crea il service di OS
```

### 5b. VPS Hetzner (uptime maggiore)

Quando il Mac di Lorenzo non basta più (e.g. scraping notturni, clienti pilot
che ne dipendono):

```bash
# Sul tuo dev box, SSH al VPS appena provisionato:
ssh root@<vps-ip>

# ─── Setup utente non-root ─────────────────────────────────────
adduser --system --group --shell /bin/bash --home /opt/savantsdatabas savantsdatabas
install -d -o savantsdatabas -g savantsdatabas /etc/savantsdatabas

# ─── Deps di sistema ──────────────────────────────────────────
apt update && apt install -y python3.12 python3.12-venv python3-pip \
                              git nginx certbot python3-certbot-nginx \
                              build-essential libxml2-dev libxslt1-dev libpq-dev

# ─── Clone repo come utente savantsdatabas ─────────────────────
sudo -u savantsdatabas -i
cd /opt/savantsdatabas
git clone https://github.com/savantmedia/savantsdatabas.git .   # adatta URL
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # ~150 MB
exit   # torna root

# ─── Env file ──────────────────────────────────────────────────
cp /opt/savantsdatabas/backend/.env.example /etc/savantsdatabas/backend.env
nano /etc/savantsdatabas/backend.env    # riempi i secret
chmod 600 /etc/savantsdatabas/backend.env
chown savantsdatabas:savantsdatabas /etc/savantsdatabas/backend.env

# ─── systemd unit ──────────────────────────────────────────────
cp /opt/savantsdatabas/deploy/systemd/savantsdatabas-backend.service \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now savantsdatabas-backend
systemctl status savantsdatabas-backend
journalctl -u savantsdatabas-backend -f   # tail dei logs

# ─── Nginx + HTTPS ─────────────────────────────────────────────
cp /opt/savantsdatabas/deploy/nginx/savantsdatabas.conf \
   /etc/nginx/sites-available/savantsdatabas
ln -s /etc/nginx/sites-available/savantsdatabas /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Genera cert Let's Encrypt (sostituisci dominio reale):
certbot --nginx -d api.savantsdatabas.savantmedia.se \
        --redirect --agree-tos -m lorenzo@savantmedia.se --non-interactive
```

Verifica end-to-end:

```bash
curl https://api.savantsdatabas.savantmedia.se/health
# → {"status":"ok","version":"1.1.0"}
```

#### Update workflow (VPS)

```bash
sudo -u savantsdatabas -i
cd /opt/savantsdatabas
git pull
cd backend && .venv/bin/pip install -r requirements.txt
exit
systemctl restart savantsdatabas-backend
```

### 5c. Docker (opzione futura)

Per chi vuole isolamento totale, o per VPS dove non vuoi installare Python
direttamente:

```bash
# Sul VPS:
cd /opt/savantsdatabas
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f backend
```

Nginx continua a fare reverse proxy verso `127.0.0.1:8000` esattamente come
nel caso systemd: il container espone solo su loopback (vedi `ports:` in
`docker-compose.prod.yml`).

> **Trade-off Docker vs systemd diretto**:
> - **Docker**: aggiornamenti più "atomici", isolamento, ma 200 MB+ di overhead di immagini.
> - **systemd + venv**: lighter, debugging più immediato (file logs, `ps`, ecc.).
>
> Per Savantsdatabas Fase 17 → **systemd è OK**, Docker è "opzione futura".

---

## 6. Step 4 — Self-hosted services (opzionali)

### SearXNG (motore di ricerca privato)

Usato dall'agente per discovery senza esaurire quote SerpAPI.

```bash
docker run -d --name savantsdatabas-searxng \
    -p 8888:8080 \
    --restart unless-stopped \
    -v /opt/searxng-config:/etc/searxng \
    searxng/searxng:latest
```

Poi setta in `backend/.env`: `SEARXNG_URL=http://localhost:8888`.

### Redis (job queue futura, Fase 6+)

```bash
docker run -d --name savantsdatabas-redis \
    -p 6379:6379 \
    --restart unless-stopped \
    -v redis-data:/data \
    redis:7-alpine \
    redis-server --appendonly yes
```

In `backend/.env`: `REDIS_URL=redis://localhost:6379/0`.

### Ollama (LLM reasoning + embeddings)

```bash
# CPU-only: funziona ma è lento (15-30s per LLM call su CX22).
docker run -d --name savantsdatabas-ollama \
    -p 11434:11434 \
    --restart unless-stopped \
    -v ollama-data:/root/.ollama \
    ollama/ollama

# Pull dei modelli:
docker exec savantsdatabas-ollama ollama pull llama3.1:8b
docker exec savantsdatabas-ollama ollama pull nomic-embed-text
```

> **Hardware reality check**: Ollama CPU-only su CX22 (2 vCPU, 4 GB) è
> _funzionale_ ma genererà bottleneck. Se l'inferenza LLM è critica → Ollama
> sul Mac di Lorenzo (GPU) e backend lo contatta via Tailscale; oppure usa
> Groq cloud (free tier generoso, `GROQ_API_KEY` già nel template).

---

## 7. Step 5 — Playwright per T4/T5

I tier di scraping più "aggressivi" usano Chromium headless.

```bash
# Sul backend (locale Windows o VPS):
cd backend
.venv/bin/python -m playwright install chromium          # Linux
.venv\Scripts\python.exe -m playwright install chromium  # Windows

# Su Linux server senza display, possono servire libs aggiuntive:
.venv/bin/python -m playwright install-deps chromium     # apt-get equivalent
```

### Risorse necessarie

| Risorsa | Per istanza headless | Note |
|---|---|---|
| RAM | ~500 MB picco | Chiudere browser fra job. |
| Disk | ~150 MB binari | Una volta sola. |
| CPU | 1 core saturabile | Throttle con `max_concurrent_jobs` nel backend. |

Su CX22 (4 GB RAM) tieni **massimo 2 browser concurrent** se hai anche
Ollama+Redis+SearXNG. Vedi `backend/scrapers/` per il limit configurabile.

---

## 8. Monitoring & ops

### Quotidiano

| Cosa | Dove |
|---|---|
| Stato DB, query log, slow queries | Supabase Studio → Database → Query Performance |
| Deploy frontend, errori SSR | Vercel dashboard → Project → Deployments / Functions |
| Backend logs realtime | `journalctl -u savantsdatabas-backend -f` |
| Backend logs Docker | `docker compose logs -f backend` |
| Health endpoint | `curl https://api.savantsdatabas.savantmedia.se/health` |

### Settimanale

- **Supabase Advisors** → `Database → Advisors`: lint su RLS missing, indici inutilizzati, etc.
- **Vercel Analytics free** → traffic + Core Web Vitals (privacy-friendly).
- **`SELECT count(*) FROM companies WHERE updated_at > now() - interval '7 days';`** → sanity check su scraping.

### Uptime monitoring (free)

Uptime Robot https://uptimerobot.com — 50 monitor free, ping ogni 5 minuti.
Crea monitor su:

- `https://db.savantmedia.se/` (frontend)
- `https://api.savantsdatabas.savantmedia.se/health` (backend)
- `https://tttcbwquiexahcmcuech.supabase.co/rest/v1/` (Supabase, attende 401 → "up")

---

## 9. Disaster recovery

### Backup automatici (free)

- **Supabase**: backup giornaliero, retention 7 giorni (free tier).
- Console: **Database → Backups → Restore** se serve.

### Backup manuali extra

```bash
# Una tantum, prima di operazioni rischiose (migrazione schema, mass update):
supabase db dump --db-url "postgresql://postgres:PWD@db.tttcbwquiexahcmcuech.supabase.co:5432/postgres" \
                 > backup-$(date +%Y-%m-%d).sql

# Oppure pg_dump diretto (richiede password DB della 3.1):
pg_dump "postgresql://postgres:PWD@db.tttcbwquiexahcmcuech.supabase.co:5432/postgres" \
        --no-owner --no-privileges \
        > backup-$(date +%Y-%m-%d).sql
```

### Restore

```bash
# Su un progetto Supabase di test (mai sul prod direttamente!):
psql "postgresql://postgres:PWD@<NEW_PROJECT>.supabase.co:5432/postgres" \
     < backup-2026-05-27.sql
```

### Test recovery — ogni 3 mesi

1. Crea progetto Supabase di staging (gratis).
2. Restore l'ultimo dump.
3. Lancia frontend localmente puntando al progetto staging.
4. Verifica: login funziona, `/companies` lista, `/orchestrator/memory` cerca.
5. Distruggi il progetto staging.

Annota data del test in `docs/ARCHITECTURE.md` o nel vault.

---

## 10. Security checklist pre-prod

Da eseguire **prima** di girare l'URL ai colleghi.

- [ ] Migration `enable_rls_phase4` applicata (`supabase migration list` mostra "Applied")
- [ ] Migration `enable_public_grants` applicata — **deadline 2026-10-30!**
- [ ] Password DB resettata dopo provisioning (3.1)
- [ ] `SUPABASE_SECRET_KEY` **non** è nel bundle client:
      ```bash
      cd .next/static && grep -r "sb_secret_" . && echo "LEAK!" || echo "OK"
      grep -r "service_role" .next/static && echo "LEAK!" || echo "OK"
      ```
- [ ] `ORCHESTRATOR_API_TOKEN` generato random 64-hex (no default, no string parlante)
- [ ] HTTPS attivo:
      - Vercel: automatico
      - Backend VPS: `curl -I https://api.savantsdatabas.savantmedia.se/health` → `HTTP/2 200`
      - Cert Let's Encrypt auto-renew abilitato (`systemctl status certbot.timer`)
- [ ] Tailscale ACL configurato (se usato VPN): solo device di Savant Media
- [ ] Backup manuale di pre-deploy salvato in posto sicuro (`backup-pre-prod.sql`)
- [ ] Test di restore eseguito almeno una volta su staging
- [ ] Supabase Auth: dominio email limitato a `@savantmedia.se` (o policy custom)
- [ ] Logs verificati: niente `print(secret_key)` o leak nei traceback
      ```bash
      journalctl -u savantsdatabas-backend --since "1 hour ago" | grep -iE "(secret|password|token)" \
        | grep -v "OK" && echo "POSSIBLE LEAK" || echo "OK"
      ```
- [ ] `.env.local` e `backend/.env` confermati gitignored (`git check-ignore .env.local backend/.env`)
- [ ] Rate-limit Nginx attivo (sezione `limit_req` in `deploy/nginx/savantsdatabas.conf`)
- [ ] `ORCHESTRATOR_API_TOKEN` verifica server-side in tutte le route `/orchestrator/*`
- [ ] Supabase RLS test: prova a `SELECT * FROM enrichment_jobs` con anon key → deve fallire o filtrare per user

---

## 11. Rollback plan

### Frontend (Vercel)

- Dashboard → Deployments → trova il deploy precedente → **"Promote to Production"**. 1 click, ~30s.
- Alternativa CLI: `vercel rollback <deployment-url>`.

### Backend (VPS systemd)

```bash
sudo -u savantsdatabas -i
cd /opt/savantsdatabas
git log --oneline -10                   # trova il commit/tag stabile
git checkout <commit-or-tag>
cd backend && .venv/bin/pip install -r requirements.txt
exit
systemctl restart savantsdatabas-backend
systemctl status savantsdatabas-backend
```

Buona pratica: prima di ogni deploy serio, crea un tag git:

```bash
git tag -a deploy/2026-05-27 -m "Pre fase-17 release"
git push origin deploy/2026-05-27
```

### Backend (Docker)

```bash
cd /opt/savantsdatabas
git checkout <previous-tag>
docker compose -f docker-compose.prod.yml up -d --build
```

### Database (Supabase)

1. **Database → Backups** → seleziona snapshot precedente.
2. **Restore** (sostituisce il DB corrente — è destructive).
3. **Importante**: scrivi al team prima, c'è downtime di ~2 minuti.

Per recovery point-in-time (PITR) servirebbe Pro plan; sul free hai solo
snapshot giornalieri.

---

## Appendice A — Variabili d'ambiente quick reference

### Frontend (`.env.local` / Vercel)

| Var | Esempio | Note |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | `https://tttcbwquiexahcmcuech.supabase.co` | esposta al client |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_...` | esposta al client, ok |
| `SUPABASE_SECRET_KEY` | `sb_secret_...` | **server-only** (bypassa RLS) |
| `NEXT_PUBLIC_ORCHESTRATOR_URL` | `https://api.savantsdatabas.savantmedia.se` | esposta al client |
| `ORCHESTRATOR_API_TOKEN` | `<64 hex>` | server-only, auth verso backend |

### Backend (`backend/.env` / `/etc/savantsdatabas/backend.env`)

| Var | Esempio | Note |
|---|---|---|
| `SUPABASE_URL` | `https://tttcbwquiexahcmcuech.supabase.co` | |
| `SUPABASE_SECRET_KEY` | `sb_secret_...` | bypassa RLS, server-only |
| `ORCHESTRATOR_API_TOKEN` | `<64 hex>` | stesso del frontend, controllato in middleware |
| `BOLAGSVERKET_BULK_URL` | (default) | T0 import |
| `SCB_BULK_URL` | (default) | T0 import |
| `OLLAMA_BASE_URL` | `http://localhost:11434` o Tailscale IP | T3 LLM |
| `OLLAMA_MODEL_REASONING` | `llama3.1:8b` | |
| `GROQ_API_KEY` | opzionale fallback cloud | |
| `REDIS_URL` | `redis://localhost:6379/0` | Fase 6+ |
| `SEARXNG_URL` | `http://localhost:8888` | discovery |
| `VAULT_PATH` | path al vault Obsidian | Fase 11 memory |

---

## Appendice B — Ordine raccomandato per il primo deploy

Per Lorenzo, in ordine cronologico, la prima volta:

1. **Oggi (30 min)**: applica migration RLS + GRANTs (sezione 3.2). Reset password DB (3.1).
2. **Oggi (15 min)**: configura Vercel link + env vars (4a) — senza deploy ancora.
3. **Oggi (5 min)**: verifica `npm run build` localmente → fixa eventuali type error (vedi sezione 12 `next.config.ts` standalone).
4. **Oggi (5 min)**: `vercel deploy --prod` → ottieni URL Vercel.
5. **Domani (1 h)**: provisioning VPS Hetzner CX22 + setup utente + clone repo + systemd + Nginx + cert (5b).
6. **Domani (10 min)**: aggiorna `NEXT_PUBLIC_ORCHESTRATOR_URL` su Vercel con dominio VPS, redeploy.
7. **Domani (15 min)**: esegui security checklist (sezione 10) tutta verde.
8. **Dopo**: girare il dominio `db.savantmedia.se` ai colleghi.

In alternativa (zero costi, ok per le prime settimane):

- step 5-6 sostituito da: backend sul Mac di Lorenzo + Tailscale (5a).
- Costo: 0 EUR/mese. Trade-off: il Mac di Lorenzo deve restare acceso.

---

_Ultimo aggiornamento: 2026-05-27 · Fase 17 della roadmap._
