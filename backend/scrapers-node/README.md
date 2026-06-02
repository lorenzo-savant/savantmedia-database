# scrapers-node

Node.js fallback scrapers per `savantmedia-database`. Usati come **escalation
chain** quando i pipeline Python (BeautifulSoup + httpx) vengono rate-limited
o falliscono.

## Filosofia

> Lorenzo regola del progetto (vault 2026-06-02):
> "Se Python fallisce, prova Node. Costo zero. L'obiettivo è trovare i dati,
> non difendere un linguaggio."

Per casi specifici Cheerio (selettori CSS jQuery-style) + axios sono più
ergonomici per:

- **Brave Search HTML** quando il parser BS4 manca selettori aggiornati
- **Google dorking** (`site:` + `intext:`) — Node retry handling è più snello
- **Email harvesting via SERP** dove i selettori CSS cambiano spesso

I CLI sono richiamabili come subprocess da Python:

```python
import subprocess
result = subprocess.run(
    ["node", "scrapers-node/find_emails.js", "savantmedia.se"],
    capture_output=True, text=True, timeout=60,
)
emails = json.loads(result.stdout)
```

## Setup

```bash
cd backend/scrapers-node
npm install
```

Le dependencies (axios, cheerio) sono leggere (~2 MB), nessuna API key.

## Scripts disponibili

| Script | Input | Output (stdout JSON) |
|---|---|---|
| `find_emails.js <domain>` | dominio (es. `savantmedia.se`) | array di email `[..@domain]` indicizzate da Brave/Bing/Ecosia |
| `find_dm_email.js <name> <domain>` | nome persona + dominio | array di email match per quella persona |

Tutti gli output sono ARRAY JSON su `stdout`. Errori su `stderr` + exit code != 0.

## Rate-limit handling

Ogni client (Brave/Bing/Ecosia) ha:
- UA rotating (10 desktop Chromium reali)
- Accept-Language `sv-SE`
- Backoff esponenziale 429/503 (3 tentativi, 2s/4s/8s)
- Timeout 15s per request

Se TUTTI i motori falliscono dopo retries, exit 2 (Python fallback decide
cosa fare).
