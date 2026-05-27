# Deploy — quick-start

Scegli il tuo scenario, poi salta alla sezione di `docs/DEPLOY.md` indicata.

| Scenario | Riferimento |
|---|---|
| Dev locale Lorenzo (Next.js dev + uvicorn locale) | `README.md` (root) |
| Frontend prod su Vercel | `docs/DEPLOY.md` § 4a |
| Frontend self-host con Docker | `docs/DEPLOY.md` § 4b + `Dockerfile.frontend` |
| Backend prod su VPS Hetzner | `docs/DEPLOY.md` § 5b + `deploy/systemd/` + `deploy/nginx/` |
| Backend prod sul Mac di Lorenzo via Tailscale | `docs/DEPLOY.md` § 5a |
| Full-stack Docker (testing) | `docker-compose.yml` |
| Backend prod Docker su VPS | `docker-compose.prod.yml` |
| Self-host SearXNG / Redis / Ollama | `docs/DEPLOY.md` § 6 |
| Checklist sicurezza pre-prod | `docs/DEPLOY.md` § 10 |
| Disaster recovery | `docs/DEPLOY.md` § 9 |
| Rollback | `docs/DEPLOY.md` § 11 |

## File in questa cartella

- `systemd/savantsdatabas-backend.service` — unit per uvicorn dietro Nginx sul VPS.
- `nginx/savantsdatabas.conf` — reverse proxy HTTPS + rate-limit + WebSocket.

## File correlati nel repo root

- `Dockerfile.frontend` — build Next.js standalone (self-host opzionale).
- `backend/Dockerfile` — build FastAPI orchestrator.
- `docker-compose.yml` — full-stack locale (dev/test).
- `docker-compose.prod.yml` — backend + searxng + redis sul VPS.

## Primo deploy: sequenza minima

1. Applicare le migration Supabase pendenti (sezione 3 del DEPLOY).
2. `vercel link` + `vercel env add ...` + `vercel deploy --prod` (sezione 4a).
3. Tirare su il backend (locale via Tailscale **o** VPS systemd — sezione 5).
4. Eseguire la security checklist (sezione 10) prima di girare l'URL ai colleghi.
