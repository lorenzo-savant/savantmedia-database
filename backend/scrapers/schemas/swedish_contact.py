"""
JSON-Schema-style extraction schemas for crawl4ai's `LLMExtractionStrategy`.

These schemas are fed to the LLM (Ollama / Groq via crawl4ai) together with
the page markdown so the model returns structured JSON instead of free-form
text. The shape mirrors what we eventually want to insert into
`public.companies` and `public.contacts` — see `docs/ARCHITECTURE.md` §5.

Naming convention: keys in the resulting JSON stay in Swedish where the
domain term is Swedish (organisationsnummer, stad, postnummer, bransch...)
so downstream code can reconcile against Bolagsverket/SCB fields without
translation layers. English-named keys are used where the field has no
established Swedish term (e.g. `domain`).
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Full contact + company schema — for a company's own "Om oss / Kontakt" pages
# ─────────────────────────────────────────────────────────────────────────────

SWEDISH_CONTACT_EXTRACTION_SCHEMA: dict[str, Any] = {
    "name": "swedish_company_contacts",
    # `baseSelector` is intentionally left as the document root — we rely on
    # the LLM to traverse the markdown rather than CSS selectors, because
    # company sites have wildly inconsistent DOM structure.
    "baseSelector": "body",
    "fields": [
        {
            "name": "company_name",
            "type": "string",
            "description": "Företagets fullständiga namn (juridisk form, t.ex. 'Savant Media AB').",
        },
        {
            "name": "organisationsnummer",
            "type": "string",
            "description": "Org.nr i format XXXXXX-XXXX. Lämna tomt om ej angivet.",
        },
        {
            "name": "domain",
            "type": "string",
            "description": "Huvuddomän, utan https:// och utan inledande www. (t.ex. 'savantmedia.se').",
        },
        {
            "name": "reception_telefon",
            "type": "string",
            "description": "Huvudtelefon/växel — INTE personliga mobilnummer.",
        },
        {
            "name": "email_info",
            "type": "string",
            "description": "Generell e-postadress, t.ex. info@, kontakt@, hej@.",
        },
        {
            "name": "stad",
            "type": "string",
            "description": "Sätesstad / huvudkontorets ort.",
        },
        {
            "name": "postnummer",
            "type": "string",
            "description": "Svenskt postnummer, format 'XXX XX' eller 'XXXXX'.",
        },
        {
            "name": "adress_gata",
            "type": "string",
            "description": "Gatuadress till huvudkontoret (utan ort/postnummer).",
        },
        {
            "name": "antal_anstallda",
            "type": "integer",
            "description": "Antal anställda om angivet på sidan, annars utelämna.",
        },
        {
            "name": "kontakter",
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "namn": {
                        "type": "string",
                        "description": "Förnamn Efternamn.",
                    },
                    "roll": {
                        "type": "string",
                        "description": "Yrkesroll eller titel, t.ex. VD, CTO, CFO, Marknadschef.",
                    },
                    "email": {
                        "type": "string",
                        "description": (
                            "Professionell e-post (förnamn.efternamn@domän). "
                            "Inkludera INTE info@/kontakt@/hej@ — dessa hör till email_info."
                        ),
                    },
                    "telefon": {
                        "type": "string",
                        "description": "Personlig direktnummer eller mobiltelefon.",
                    },
                    "linkedin_url": {
                        "type": "string",
                        "description": "Fullständig LinkedIn-profil-URL om angiven.",
                    },
                },
            },
            "description": (
                "Beslutsfattare och ledningspersoner. Max 5 personer — prioritera "
                "VD, CTO, CFO, ägare, marknadschef. Hoppa över supportpersoner och "
                "receptionister."
            ),
        },
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Lighter schema — for allabolag.se / proff.se style summary pages
# ─────────────────────────────────────────────────────────────────────────────
#
# These directory sites already show industry + size in a structured panel,
# so we don't need the full contact schema; we just want bransch + size
# so the reconcile node can complete missing SCB fields.

SWEDISH_BRANSCH_EXTRACTION_SCHEMA: dict[str, Any] = {
    "name": "swedish_company_bransch",
    "baseSelector": "body",
    "fields": [
        {
            "name": "company_name",
            "type": "string",
            "description": "Företagsnamn som det visas i sidans rubrik.",
        },
        {
            "name": "organisationsnummer",
            "type": "string",
            "description": "Org.nr i format XXXXXX-XXXX.",
        },
        {
            "name": "bransch",
            "type": "string",
            "description": (
                "Branschtext som visas på sidan, t.ex. "
                "'Dataprogrammering, konsultverksamhet'. "
                "Använd exakt vad sidan skriver — översätt inte."
            ),
        },
        {
            "name": "sni_kod",
            "type": "string",
            "description": "5-siffrig SNI-kod om angiven, t.ex. '62010'.",
        },
        {
            "name": "antal_anstallda",
            "type": "integer",
            "description": "Antal anställda (heltal).",
        },
        {
            "name": "storleksklass",
            "type": "string",
            "description": (
                "Storlekskategori om sidan visar en, t.ex. '10-49 anställda'. "
                "Annars utelämna."
            ),
        },
        {
            "name": "omsattning_tkr",
            "type": "integer",
            "description": "Omsättning i tusentals kronor (tkr), senaste året.",
        },
    ],
}


__all__ = [
    "SWEDISH_CONTACT_EXTRACTION_SCHEMA",
    "SWEDISH_BRANSCH_EXTRACTION_SCHEMA",
]
