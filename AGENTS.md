## Working rules (per l'agente)

- Diff piccoli e revisionabili. Un task = un PR logico.
- Niente segreti in codice: tutto via env (.env / Supabase secrets). Mai loggare credenziali o API key.
- Ogni tier/funzione nuova nasce con: test + validazione output + logging strutturato.
- Cache-first SEMPRE: prima di scrapare controlla DB + TTL di freschezza (enriched_at).
- Si escala al tier successivo SOLO su segnale di fallimento esplicito (403/503, captcha, body di blocco, risultato vuoto, validazione fallita).
- Persist solo dopo normalizzazione nello schema canonico + provenance (quale tier ha prodotto il dato).
- I tier lenti (T4/T5) sono job async in scrape_jobs, non chiamate bloccanti.
- RLS: ogni nuova tabella/colonna richiede policy esplicita prima del merge.
- Dedup company per numero org Bolagsverket (upsert idempotente).
