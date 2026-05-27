"use client";

import { Sparkles, Globe, Users, Phone, Mail, Database, RefreshCw } from "lucide-react";

export type PromptSuggestion = {
  id: string;
  label: string;
  category: "domain" | "size" | "contacts" | "dm" | "quality" | "bulk";
  prompt: string;
  description: string;
};

export const PROMPT_SUGGESTIONS: PromptSuggestion[] = [
  // ── Domain discovery ────────────────────────────────────────
  {
    id: "domain-50-stockholm",
    label: "Hitta 50 domäner — Stockholm",
    category: "domain",
    description: "500 företag från Bolagsverket bulk saknar domain. Börja här.",
    prompt:
      "Hitta huvuddomän (officiell webbplats) för 50 aktiva AB i Stockholms län som saknar domain-fält i databasen. Använd T1 SearXNG-sökning med queries som '<foretagsnamn> Stockholm site:.se' och verifiera att hemsidan returneras i T2-fetch. Spara domänen normaliserad (utan https://, utan www., utan trailing slash). Skip om ingen tydlig hemsida hittas — bättre tomt än fel.",
  },
  {
    id: "domain-konsult",
    label: "Domäner — konsult/Group AB",
    category: "domain",
    description: "Mid-size konsultbolag, mer sannolikt välmade webbsidor.",
    prompt:
      "Hitta domäner för 30 aktiva AB där företagsnamnet innehåller 'konsult', 'Group' eller 'Sverige'. Använd SearXNG. Verifiera att domänen är levande (HTTP 200 från homepage via T2). Spara source_url med tier=1.",
  },

  // ── Storlek / bransch ───────────────────────────────────────
  {
    id: "size-allabolag",
    label: "Antal anställda via allabolag (T4)",
    category: "size",
    description: "Stealth på /bransch-sök (lezione vault — /foretag/ è SPA bloccato).",
    prompt:
      "För 20 företag i Stockholms län som saknar antal_anstallda, sök på allabolag.se/bransch-sök efter företagsnamnet och extrahera anställda-range från träfflistan (T4 Playwright stealth med storage_state). VIKTIGT: allabolag.se/foretag/* är React SPA och blockerar scraping — använd ENDAST listsidor. Spara antal_anstallda som mittpunkt av range. Härled storlek_kategori automatiskt.",
  },
  {
    id: "size-scb-bulk",
    label: "Bransch via SCB öppna data (T0)",
    category: "size",
    description: "Cross-reference med SCB bulk — gratis, ingen scraping.",
    prompt:
      "För alla företag med organisationsnummer men utan bransch-fält, hämta SNI-koder från SCB öppna data bulkfil (T0). SCB bulk innehåller org.nr + SNI-kod + verksamhetskategori. Filtrera till AB. Mata in i metadata under 'bransch_sni' och 'bransch_text'.",
  },

  // ── Generella kontakter ─────────────────────────────────────
  {
    id: "contact-info",
    label: "Centralino + info@ — 30 företag",
    category: "contacts",
    description: "Hämta /kontakt + /om-oss på domänen.",
    prompt:
      "För 30 företag som har domain men saknar reception_telefon ELLER email_info, hämta /kontakt, /kontakta-oss, /om-oss från företagets domän (T2 httpx+BS). Extrahera huvudtelefon (svenskt format +46) och info@/kontakt@-mail. Respektera robots.txt. Spara source_url för audit.",
  },

  // ── Decision Makers (B2B enrichment) ────────────────────────
  {
    id: "dm-multinationals",
    label: "VD för 20 multinationals",
    category: "dm",
    description: "B2B enrichment validato (Spotify, Klarna, etc.).",
    prompt:
      "Använd B2B enrichment-pipelinen för 20 multinationella företag i databasen. För varje företag: (1) SearXNG-sökning '<foretagsnamn> VD email', (2) Hämta /kontakt, /om-oss, /team, (3) Extrahera namn nära e-postadresser, (4) Reconcile via email_verification (reject info@/kontakt@/hej@), (5) Critic-node verifierar, (6) Spara endast verifierade kontakter. Maximalt 3 per företag.",
  },
  {
    id: "dm-it-konsult-mid",
    label: "CTO + CFO IT-konsult medel",
    category: "dm",
    description: "Per le 9 IT-konsult medie già nel DB.",
    prompt:
      "För 15 medel-stora IT-konsultföretag (50-249 anställda) i Stockholm, hitta CTO och CFO med: professionell e-post (matchar företagets domän), LinkedIn-profil, källa från /team eller LinkedIn public. Använd B2B enrichment. Verifiera med critic-node. Markera verifierad=true ENDAST om e-post är textuellt synlig (INTE pattern-genererad).",
  },
  {
    id: "dm-small-founder",
    label: "Founder/VD piccole aziende",
    category: "dm",
    description: "Liten = grundare = enda DM. 1 contatto basta.",
    prompt:
      "För små företag (antal_anstallda 0-49) — typiskt grundare = VD = enda DM. Sätt sok_fler_kontakter=false efter att grundaren hittats. Använd B2B enrichment med max 1 verifierad kontakt per företag. Inkludera LinkedIn-profil när tillgänglig.",
  },

  // ── Quality ─────────────────────────────────────────────────
  {
    id: "quality-reverify",
    label: "Re-verifica contatti >90 dagar",
    category: "quality",
    description: "Cross-check LinkedIn current employer.",
    prompt:
      "För kontakter där verifierat_datum är äldre än 90 dagar, kör reconcile + critic igen för att se om personen fortfarande arbetar på företaget. Om LinkedIn-profilen visar ny arbetsgivare → markera verifierad=false + skapa intern anteckning.",
  },
  {
    id: "quality-dedup",
    label: "Cleanup duplicate org.nr",
    category: "quality",
    description: "Paranoia check — unique constraint dovrebbe gestire.",
    prompt:
      "Sök efter aktiva företag med identiskt organisationsnummer. Slå ihop deras kontakter under den äldsta posten och arkivera dubbletten. Ingen scraping — bara DB-logik.",
  },

  // ── Bulk ────────────────────────────────────────────────────
  {
    id: "bulk-skane",
    label: "Bulk Skåne 200 AB",
    category: "bulk",
    description: "T0 open data — espandi a sud.",
    prompt:
      "Importera 200 ytterligare AB från bulk Bolagsverket för Skåne län (postnummer prefix 20-29). Använd backend/scripts/import_bolagsverket_bulk.py apply --region 'Skåne län' --limit 200. T0 öppna data, CC-BY-4.0, gratis.",
  },
];

const CATEGORY_META: Record<
  PromptSuggestion["category"],
  { label: string; icon: React.ReactNode; color: string }
> = {
  domain: {
    label: "Domän",
    icon: <Globe className="w-3 h-3" />,
    color: "bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100",
  },
  size: {
    label: "Storlek",
    icon: <Users className="w-3 h-3" />,
    color: "bg-violet-50 text-violet-700 border-violet-200 hover:bg-violet-100",
  },
  contacts: {
    label: "Kontakt",
    icon: <Phone className="w-3 h-3" />,
    color: "bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100",
  },
  dm: {
    label: "DM",
    icon: <Mail className="w-3 h-3" />,
    color: "bg-amber-50 text-amber-800 border-amber-200 hover:bg-amber-100",
  },
  quality: {
    label: "Kvalitet",
    icon: <RefreshCw className="w-3 h-3" />,
    color: "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100",
  },
  bulk: {
    label: "Bulk",
    icon: <Database className="w-3 h-3" />,
    color: "bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100",
  },
};

type PromptSuggestionsProps = {
  onPick: (prompt: string) => void;
  disabled?: boolean;
};

export function PromptSuggestions({ onPick, disabled }: PromptSuggestionsProps) {
  return (
    <div className="mb-3">
      <div className="flex items-center gap-1.5 mb-2 text-xs font-bold uppercase tracking-wider text-gray-500">
        <Sparkles className="w-3 h-3" />
        Förslag (fyll i tomma fält i DB)
      </div>
      <div className="flex flex-wrap gap-1.5">
        {PROMPT_SUGGESTIONS.map((s) => {
          const meta = CATEGORY_META[s.category];
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => onPick(s.prompt)}
              disabled={disabled}
              title={s.description}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-full border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${meta.color}`}
            >
              {meta.icon}
              <span className="opacity-70 mr-0.5">{meta.label}</span>
              {s.label}
            </button>
          );
        })}
      </div>
      <p className="text-[11px] text-gray-400 mt-1.5">
        Hovra för beskrivning. Klicka för att fylla i textarea. Detaljer i{" "}
        <code className="bg-gray-100 px-1 rounded">docs/ORCHESTRATOR_PROMPTS.md</code>.
      </p>
    </div>
  );
}
