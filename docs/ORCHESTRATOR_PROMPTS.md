# Orchestrator — prompt-bibliotek

Collezione di prompt pronti all'uso per il cockpit `/orchestrator`. Ciascuno è ottimizzato per un gap specifico nei dati attualmente in Supabase.

**Stato dati al 2026-05-27** (vedi `SELECT count(*) FROM companies`):
- 590 aziende attive
- **500** senza `domain` (tutte dal bulk Bolagsverket Stockholms län)
- **500** senza `antal_anstallda` / `storlek_kategori`
- **515** senza `reception_telefon`
- **505** senza `email_info`
- **0** contatti registrati nelle 590 aziende ← il vero bottleneck

Ogni prompt sotto **specifica il filtro** (criteri SQL del target), **i campi da popolare** e **i tier consigliati**. L'agente userà il principio "prefer open data over scraping" e degraderà gracefully se SearXNG/Ollama/Playwright non sono attivi.

---

## 🎯 Categoria A — Discovery dominio (500 aziende)

Le aziende importate dal bulk Bolagsverket hanno org.nr e nome ma nessun sito web. Il dominio è critico perché:
- è la base per il pattern email (`fornamn.efternamn@dominio.se`)
- sblocca tutto l'enrichment successivo (T2/T3 sulla homepage)

### A1. Domain discovery — batch 50 Stockholm

```
Hitta huvuddomän (officiell webbplats) för 50 aktiva AB i Stockholms län
som saknar domain-fält i databasen. Använd T1 SearXNG-sökning med queries
som "<foretagsnamn> Stockholm site:.se" och verifiera att hemsidan returneras
i T2-fetch. Spara domänen normaliserad (utan https://, utan www., utan
trailing slash). Skip om ingen tydlig hemsida hittas — bättre tomt än fel.
```

**Filtro**: `WHERE NOT arkiverad AND (domain IS NULL OR domain = '') AND region = 'Stockholms län' LIMIT 50`
**Campi**: `domain`
**Tier**: T1 (SearXNG) → T2 (verifica fetch homepage)

### A2. Domain discovery — focus PMI medie

Per aziende con presunta dimensione media (più probabilità di sito ben fatto):

```
Hitta domäner för aktiva AB där företagsnamnet innehåller en av:
"konsult", "AB", "Group", "Sverige", "Stockholm". Använd SearXNG.
Verifiera att domänen är levande (HTTP 200 från homepage via T2).
Maximalt 30 företag per körning. Spara source_url i sources med tier=1.
```

**Filtro**: `WHERE NOT arkiverad AND domain IS NULL AND (foretagsnamn ILIKE '%konsult%' OR foretagsnamn ILIKE '%Group%') LIMIT 30`

---

## 📊 Categoria B — Discovery dimensione + bransch

`antal_anstallda` non è nel bulk Bolagsverket (è dietro paywall su allabolag). Strategie:

### B1. Antal anställda da allabolag (lezione vault: solo bransch-sök, NON /foretag/)

```
För 20 företag i Stockholms län som saknar antal_anstallda, sök på
allabolag.se/bransch-sök efter företagsnamnet och extrahera anställda-range
från träfflistan (T4 Playwright stealth med storage_state). VIKTIGT (vault-
läxa): allabolag.se/foretag/* är React SPA och blockerar scraping — använd
ENDAST listsidor (/bransch-sök). Spara antal_anstallda som mittpunkt av
range (t.ex. "20-49" → 35). Härled storlek_kategori automatiskt.
```

**Filtro**: `WHERE antal_anstallda IS NULL AND organisationsnummer IS NOT NULL AND region = 'Stockholms län' LIMIT 20`
**Tier**: T4 (Playwright stealth, storage_state per cookie consent)
**Vault lesson**: codificata in `backend/scrapers/_allabolag_strategy.py`

### B2. Bransch via SCB öppna data

```
För alla företag med organisationsnummer men utan bransch-fält, hämta
SNI-koder från SCB öppna data bulkfil (T0). Schema: SCB bulk innehåller
org.nr + SNI-kod + verksamhetskategori. Filtrera till AB. Mata in i metadata
under "bransch_sni" och "bransch_text".
```

**Filtro**: tutte le `organisationsnummer NOT NULL` con `bransch` vuoto
**Tier**: T0 (bulk file SCB, gratis CC-BY-4.0)
**Nota**: richiede `backend/scripts/import_bolagsverket_bulk.py apply --source scb`

---

## 📞 Categoria C — Contatti generali (centralino + info-email)

### C1. Kontaktuppgifter da /kontakt e /om-oss

```
För 30 företag som har domain men saknar reception_telefon ELLER email_info,
hämta /kontakt, /kontakta-oss, /om-oss från företagets domän (T2 httpx+BS).
Extrahera huvudtelefon (svenskt format +46) och info@/kontakt@-mail.
Respektera robots.txt. Spara även source_url för audit.
```

**Filtro**: `WHERE domain IS NOT NULL AND domain != '' AND (reception_telefon = '' OR email_info = '') LIMIT 30`
**Tier**: T2 (httpx+BS+trafilatura, default ON robots/rate/retry)

### C2. Verifica e correzione växel-nummer

```
För företag där reception_telefon redan finns men formatet är inkonsekvent
(saknar +46, har mellanslag fel), normalisera till +46 8 NNN NN NN format.
Detta är en datafix-körning — ingen scraping behövs, bara pipeline.reconcile.
```

**Filtro**: `WHERE reception_telefon NOT LIKE '+46%' AND reception_telefon != ''`
**Tier**: ingen scraping — solo reconcile/normalize

---

## 👥 Categoria D — Beslutsfattare (il vero valore)

La pipeline B2B Contact Enrichment validata di Lorenzo (292/548 verified, vault `🕷️ Web Scraping & SERP#🏆 Pipeline Validata`). Cablata in `backend/pipeline/b2b_enrichment.py`.

### D1. VD + email per 20 aziende note (multinationals)

```
Använd B2B enrichment-pipelinen för 20 multinationella företag i databasen
(Spotify, Klarna, Voi, Mojang, Epidemic Sound, ...). För varje företag:
1. SearXNG-sökning "<foretagsnamn> VD email" + "<domain> kontakt VD"
2. Hämta /kontakt, /om-oss, /team (T2)
3. Extrahera namn nära e-postadresser
4. Reconcile via email_verification (reject info@/kontakt@/hej@)
5. Critic-node verifierar (Ollama eller rule-based fallback)
6. Spara endast verifierade kontakter (verifierad=true) i contacts-tabellen
Maximalt 3 verifierade kontakter per företag.
```

**Filtro**: `WHERE storlek_kategori = 'multinationell' AND domain IS NOT NULL LIMIT 20`
**Tier**: SPECIAL `b2b_enrichment` (combinato T1+T2 + reconcile + critic)
**Vault**: pipeline 292/548 emails validate

### D2. CTO + CFO per medium-size IT-konsult

```
För 15 medel-stora IT-konsultföretag (50-249 anställda) i Stockholm,
hitta CTO och CFO med:
- Professionell e-post (matchar företagets domän)
- LinkedIn-profil (linkedin.com/in/<slug>)
- Källa: företagets /team eller LinkedIn public page
Använd B2B enrichment. Verifiera med critic-node. Markera verifierad=true
endast om e-post är textuellt synlig i fonte (INTE pattern-genererad).
```

**Filtro**: `WHERE storlek_kategori = 'medel' AND interna_anteckningar LIKE '%IT-konsult%' LIMIT 15`
**Tier**: `b2b_enrichment`

### D3. Founder/VD per piccole aziende (≤50 dipendenti)

Per le piccole aziende il founder è spesso anche VD/CTO:

```
För små företag (antal_anstallda 0-49) — typiskt grundare = VD = enda DM.
Sätt sok_fler_kontakter=false efter att grundaren hittats, så vi inte
försöker hitta CTO/CFO som inte finns. Använd B2B enrichment med max 1
verifierad kontakt per företag.
```

**Filtro**: `WHERE storlek_kategori = 'liten' AND id NOT IN (SELECT DISTINCT company_id FROM contacts) LIMIT 25`
**Tier**: `b2b_enrichment` (con flag sok_fler_kontakter=false post-run)

---

## 🔁 Categoria E — Qualità e refresh

### E1. Re-verifica contatti vecchi (>90 giorni)

```
För kontakter där verifierat_datum är äldre än 90 dagar, kör reconcile
+ critic igen för att se om personen fortfarande arbetar på företaget.
Om LinkedIn-profilen visar ny arbetsgivare → markera verifierad=false
+ skapa intern anteckning på kontakten.
```

**Filtro**: `WHERE verifierat_datum < NOW() - INTERVAL '90 days'`
**Tier**: T2 fetch LinkedIn public + B2B enrichment partial

### E2. Cleanup duplicate org.nr (paranoia check)

```
Sök efter aktiva företag med identiskt organisationsnummer (skulle inte
hända tack vare unique constraint, men verifiera). Slå ihop deras kontakter
under den äldsta posten och arkivera dubbletten.
```

**Filtro**: SQL aggregato — `GROUP BY organisationsnummer HAVING count(*) > 1`
**Tier**: ingen scraping — solo logica DB

---

## 📋 Categoria F — Bulk Bolagsverket continuati

### F1. Espandi import bulk altre regioni

```
Importera 200 ytterligare AB från bulk Bolagsverket men för Skåne län
denna gång (postnummer prefix 20-29). Använd backend/scripts/
import_bolagsverket_bulk.py apply --region "Skåne län" --limit 200.
Detta är T0 öppna data, CC-BY-4.0, gratis.
```

**Tier**: T0 bulk (manuale CLI, non agent)

### F2. SCB cross-reference

```
För alla 500 företag importerade från Bolagsverket bulk, korsreferera med
SCB bulk för att hämta antal_anstallda (range) och SNI-bransch. Detta
fyller i 80% av de tomma fälten utan någon scraping.
```

**Tier**: T0 (offline join via DuckDB)

---

## 🎬 Come usare questi prompt nell'UI

1. Copia uno dei prompt sopra
2. Vai su [http://localhost:3000/orchestrator](http://localhost:3000/orchestrator)
3. Incolla nel textarea "Beskriv vad du vill att agenten ska hitta"
4. Clicca "Generera plan"
5. Rivedi i 3-5 step proposti — se OK, approva
6. ExecutionView in fondo mostra live progress

**Prima volta**: l'agente ora ha LLM stub (genera 3-step hardcoded). Quando wire Groq/Ollama nel `plan` node, i prompt sopra produrranno piani molto più mirati.

## 🧪 Test rapido senza Ollama/Docker

I prompt che funzionano oggi (con stub LLM):
- C1, C2 — il piano hardcoded coprirà T1+T2 ed eseguirà fetch reali
- E2 — solo SQL, nessuna dipendenza esterna

Quelli che richiedono Ollama (per LLM extraction o critic):
- D1, D2, D3 — B2B enrichment con critic step

Quelli che richiedono Playwright Chromium installato:
- B1 — T4 stealth allabolag

Quelli che richiedono Docker SearXNG:
- A1, A2 — discovery via SearXNG (degrade a "no domain found" se non gira)
