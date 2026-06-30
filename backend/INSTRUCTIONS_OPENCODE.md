# Istruzioni per OpenCode — Savantmedia DB · Email decision-maker

> Sei un agente che NON conosce questo progetto. Segui queste istruzioni alla lettera.
> Obiettivo: trovare le **email personali dei decision-maker** (VD / ägare / styrelse / chef)
> di aziende svedesi che hanno già **nome del DM + dominio web**, ma **manca l'email**.
> Stato attuale: **812 / 1000 email** (servono ~188). I nomi VD (1.738) sono già oltre obiettivo.

---

## 0. Contesto tecnico (leggi una volta)

- **Database:** Supabase (PostgreSQL). 3 tabelle: `companies` (~7.519 aziende), `contacts` (decision-maker), `sources` (audit).
- **Repo:** `c:\Users\loren\Desktop\dev-projects\lorenzo-savant repo\savantmedia-database`
- **Python backend:** cartella `backend\`, interprete venv: `backend\.venv\Scripts\python.exe` (Python 3.12).
- **Credenziali Supabase:** GIÀ presenti in `.env` e `.env.local` nella root del repo. Gli script le caricano da soli — **non devi configurare nulla**, non committare mai questi file.
- **Tutti i comandi vanno eseguiti dalla cartella `backend\`.**

### Verifica ambiente (esegui per primo)
```powershell
cd "c:\Users\loren\Desktop\dev-projects\lorenzo-savant repo\savantmedia-database\backend"
.venv\Scripts\python.exe --version          # atteso: Python 3.12.x
.venv\Scripts\python.exe -c "import duckdb, supabase, httpx; print('deps OK')"
```
Se questi due comandi funzionano, sei pronto.

---

## TASK A — Scraper cost-free (NESSUN token AI · lancialo subito, lascialo girare)

Lo script `enrich_dm_emails` trova in automatico le email dei DM noti: scarica le pagine
team/ledning/kontakt del sito aziendale + cerca su Brave/Ecosia/Bing. **Scrive direttamente
nel DB**, è **idempotente** (salta chi ha già email). Resa ~5–15%, ma gratis.

```powershell
cd "c:\Users\loren\Desktop\dev-projects\lorenzo-savant repo\savantmedia-database\backend"
.venv\Scripts\python.exe -m scripts.enrich_dm_emails --offset 0   --limit 80 --workers 3
.venv\Scripts\python.exe -m scripts.enrich_dm_emails --offset 480 --limit 80 --workers 3
.venv\Scripts\python.exe -m scripts.enrich_dm_emails --offset 960 --limit 80 --workers 3
```

**REGOLE FERREE per Task A:**
- ❗ **NON usare `--limit` maggiore di 80** → Supabase dà errore 400 ("JSON could not be generated") su range troppo grandi.
- Esegui i tre comandi **in sequenza** (uno alla volta), non in parallelo (stesso IP = blocchi 429).
- Output a video: righe `HIGH/MEDIUM ... → email` = trovata; `--` = non trovata. Alla fine una tabella riassuntiva.
- **Non serve nessun "apply"** per Task A: scrive già nel DB.

---

## TASK B — Wave "workflow" (ricerca guidata da TE con websearch · usa i TUOI token)

Qui usi i tuoi strumenti di **web search + web fetch** per trovare le email che lo scraper
automatico non prende. Lavori su una lista di aziende con DM noto + dominio, ma senza email.

### B.1 — Genera la lista di lavoro
Esegui questo (crea `data\wf_opencode.json`):
```powershell
cd "c:\Users\loren\Desktop\dev-projects\lorenzo-savant repo\savantmedia-database\backend"
@'
import os, json
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path('.').resolve(); load_dotenv(ROOT.parent/'.env'); load_dotenv(ROOT.parent/'.env.local')
from supabase import create_client
sb=create_client(os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL"), os.environ["SUPABASE_SECRET_KEY"])
def fa(t,s):
    r,st,pg=[],0,1000
    while True:
        d=sb.table(t).select(s).order("id").range(st,st+pg-1).execute()
        if not d.data: break
        r.extend(d.data)
        if len(d.data)<pg: break
        st+=pg
    return r
comps={c["id"]:c for c in fa("companies","id,foretagsnamn,domain,stad,arkiverad") if not c.get("arkiverad")}
contacts=fa("contacts","id,company_id,namn,roll,email,is_dm")
DM=("vd","verkställande","verkstallande","ägare","agare","owner","grundare","founder","ceo","cfo","cto","coo","president","styrelse","ordförande","ordforande","delägare","partner","direktör","direktor","chef")
def is_dm(r): return any(w in (r.get("roll") or "").lower() for w in DM)
by={}
for k in contacts:
    if (k.get("email") or "").strip(): continue
    if not (k.get("is_dm") or is_dm(k)): continue
    nm=(k.get("namn") or "").strip()
    if not nm or len(nm.split())<2: continue
    c=comps.get(k["company_id"])
    if not c or not (c.get("domain") or "").strip(): continue
    by.setdefault(k["company_id"], {"company_id":k["company_id"],"foretagsnamn":c["foretagsnamn"],"domain":(c.get("domain") or "").strip(),"stad":c.get("stad") or "","dms":[]})
    by[k["company_id"]]["dms"].append({"namn":nm,"roll":k.get("roll") or "VD"})
work=sorted(by.values(), key=lambda w:w["foretagsnamn"])
# OpenCode lavora dalla SECONDA METÀ per non sovrapporsi a Claude (che parte dall'inizio)
half=work[len(work)//2:]
Path("data/wf_opencode.json").write_text(json.dumps(half,ensure_ascii=False,indent=1),encoding="utf-8")
print(f"Totale lavorabili: {len(work)} aziende — OpenCode ne prende {len(half)} (seconda metà)")
'@ | .venv\Scripts\python.exe -
```
> ⚠️ **Coordinamento:** questo script dà a OpenCode la **seconda metà** della lista (Claude lavora la prima metà). Così non cercate le stesse aziende.

### B.2 — Per OGNI azienda nella lista, per OGNI persona in `dms`, cerca l'email
Usa web search + web fetch. Query consigliate (in quest'ordine, fermati appena trovi):
1. `"<Nome Cognome>" "@<dominio>"`
2. `"<Nome Cognome>" "<dominio>" e-post OR mail OR kontakt`
3. `site:<dominio> "<Nome Cognome>"`
4. Scarica direttamente: `https://<dominio>/kontakt/`, `/om-oss/`, `/ledning/`, `/medarbetare/`, `/team/`, `/about/`
5. LinkedIn: `"<Nome Cognome>" "<foretagsnamn>" linkedin`

### B.3 — REGOLE di validazione (CRITICHE — la qualità conta più della quantità)
✅ **ACCETTA** solo email che:
- sono **osservate su una pagina/snippet reale** (MAI inventate o "indovinate" a pattern),
- sono **personali sul dominio aziendale** (es. `fornamn.efternamn@dominio.se`, `fornamn@dominio.se`),
- il local-part contiene plausibilmente il nome/cognome della persona.

❌ **SCARTA** sempre:
- caselle generiche: `info@`, `kontakt@`, `post@`, `mail@`, `support@`, `office@`, `ekonomi@`, ecc.
- `gmail.com` / `hotmail.com` / `outlook.com` / `yahoo.com` su dominio aziendale,
- email "dedotte" senza prova sulla pagina.

Se non trovi un'email valida per una persona, **saltala** (non scrivere nulla per lei).

### B.4 — Scrivi i risultati in file `dm_result_*.json` (in `backend\data\`)
Un file ogni ~5 aziende, nome es. `data\dm_result_opencode_001.json`. Formato ESATTO:
```json
[
  {
    "id": "<company_id>",
    "dm": [
      {
        "namn": "Anna Andersson",
        "roll": "VD",
        "email": "anna.andersson@dominio.se",
        "email_method": "foretagswebbplats",
        "source_url": "https://dominio.se/kontakt/",
        "linkedin": "https://linkedin.com/in/anna-andersson",
        "telefon": "+46701234567"
      }
    ]
  }
]
```
- `id` = il `company_id` dell'azienda (OBBLIGATORIO).
- Metti in `dm` SOLO le persone per cui hai un'email verificata.
- `email_method`: usa `foretagswebbplats` (se dal sito), `linkedin`, o `websearch`.

### B.5 — Applica i risultati al DB
```powershell
.venv\Scripts\python.exe -m scripts.apply_dm_results
```
Aggiorna l'email dei contatti esistenti. Idempotente (salta email già presenti), filtra le generiche. Mostra `emails_set` = quante nuove email applicate.

---

## VERIFICA PROGRESSO (esegui quando vuoi)
```powershell
cd "c:\Users\loren\Desktop\dev-projects\lorenzo-savant repo\savantmedia-database\backend"
@'
import os
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path('.').resolve(); load_dotenv(ROOT.parent/'.env'); load_dotenv(ROOT.parent/'.env.local')
from supabase import create_client
sb=create_client(os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL"), os.environ["SUPABASE_SECRET_KEY"])
def fa(t,s):
    r,st,pg=[],0,1000
    while True:
        d=sb.table(t).select(s).order("id").range(st,st+pg-1).execute()
        if not d.data: break
        r.extend(d.data)
        if len(d.data)<pg: break
        st+=pg
    return r
ct=fa("contacts","id,roll,email,is_dm")
DM=("vd","verkställande","verkstallande","ägare","agare","owner","grundare","founder","ceo","cfo","cto","coo","president","styrelse","ordförande","ordforande","delägare","partner","direktör","direktor","chef")
def is_dm(r): return any(w in (r.get("roll") or "").lower() for w in DM)
dm=[x for x in ct if (x.get("is_dm") or is_dm(x))]
print("Email DM:", sum(1 for x in dm if (x.get('email') or '').strip()), "/ 1000")
'@ | .venv\Scripts\python.exe -
```

---

## REGOLE DI COORDINAMENTO (Claude lavora in parallelo)
1. **Task A (cost-free Python): lo possiede OpenCode** — fallo girare tu (Claude fa solo le wave workflow).
2. **Task B:** OpenCode lavora la **seconda metà** della lista (lo script B.1 lo fa già). Claude la prima metà.
3. Tutto è **idempotente**: `apply_dm_results` salta le email già presenti, quindi anche se vi sovrapponete non si rompe nulla e non si creano duplicati.
4. **Mai** committare o pushare nulla (lo gestisce il proprietario). Mai toccare `.env`.
5. Se uno script Python dà errore 400 Supabase → hai usato `--limit` troppo alto: riduci a ≤ 80.
