"use client";

import {
  Mail,
  UserRound,
  Phone,
  Globe,
  Building2,
  MapPin,
  Users,
  Target,
} from "lucide-react";

/**
 * Snabbfångst — en knapp per datafält i `companies` / `kontakter`.
 *
 * Varje knapp fyller i textarea med en färdig, fält-specifik prompt så att
 * användaren kan fånga exakt ett fält i taget (e-post, telefon, webbplats,
 * beslutsfattare, bransch, adress, antal anställda). Prompten väljer rätt
 * tier-strategi och riktar in sig på företag där fältet saknas.
 */

export type DataCaptureAction = {
  id: string;
  /** Datafältet i databasen knappen fyller. */
  field: string;
  label: string;
  icon: React.ReactNode;
  color: string;
  prompt: string;
};

export const DATA_CAPTURE_ACTIONS: DataCaptureAction[] = [
  {
    id: "capture-email",
    field: "email_info",
    label: "E-post",
    icon: <Mail className="w-3.5 h-3.5" />,
    color: "bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100",
    prompt:
      "Hämta allmän företagsmejl (info@/kontakt@) för 30 företag som har domain men saknar email_info. Använd den nya e-postsökningen: SERP-dork \"@<domain>\" + hämta sidorna /kontakt och /medarbetare och extrahera även de-offuskerade adresser (snabel-a, punkt, [at], [dot]). Ranka personliga adresser först; spara den bäst rankade — annars info@/kontakt@. Spara source_url för audit (tier 1.5).",
  },
  {
    id: "capture-dm",
    field: "kontakter",
    label: "Beslutsfattare",
    icon: <UserRound className="w-3.5 h-3.5" />,
    color: "bg-amber-50 text-amber-800 border-amber-200 hover:bg-amber-100",
    prompt:
      "Hitta beslutsfattare (VD, CTO, CFO) för 20 företag som har domain. Använd B2B enrichment-pipelinen: SERP-sökning \"<foretagsnamn> VD\", hämta /kontakt, /om-oss och /team, extrahera namn nära e-postadress, kör reconcile + critic. Markera verifierad=true ENDAST om e-posten är textuellt synlig (INTE pattern-genererad). Max 3 kontakter per företag.",
  },
  {
    id: "capture-phone",
    field: "reception_telefon",
    label: "Telefon (växel)",
    icon: <Phone className="w-3.5 h-3.5" />,
    color: "bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100",
    prompt:
      "Hämta huvudtelefon (växel, svenskt format +46) för 30 företag som har domain men saknar reception_telefon. Hämta /kontakt och /kontakta-oss på företagets domän (T2 httpx+BS). Respektera robots.txt. Spara source_url för audit.",
  },
  {
    id: "capture-domain",
    field: "domain",
    label: "Webbplats",
    icon: <Globe className="w-3.5 h-3.5" />,
    color: "bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100",
    prompt:
      "Hitta officiell webbplats (huvuddomän) för 50 aktiva AB som saknar domain-fält. Använd T1 SearXNG med queries som '<foretagsnamn> <stad> site:.se' och verifiera att hemsidan svarar HTTP 200 i T2-fetch. Spara domänen normaliserad (utan https://, utan www., utan trailing slash). Skip om ingen tydlig hemsida hittas.",
  },
  {
    id: "capture-bransch",
    field: "sni",
    label: "Bransch (SNI)",
    icon: <Building2 className="w-3.5 h-3.5" />,
    color: "bg-violet-50 text-violet-700 border-violet-200 hover:bg-violet-100",
    prompt:
      "Hämta SNI-kod och bransch för företag med organisationsnummer men utan bransch-fält. Använd SCB öppna data bulkfil (T0): org.nr → SNI-kod + verksamhetskategori. Filtrera till AB. Spara sni_primary_kod, sni_huvudgrupp och sni_branscher. Ingen scraping.",
  },
  {
    id: "capture-adress",
    field: "adress",
    label: "Adress",
    icon: <MapPin className="w-3.5 h-3.5" />,
    color: "bg-rose-50 text-rose-700 border-rose-200 hover:bg-rose-100",
    prompt:
      "Komplettera besöksadress (gata, postnummer, stad, län) för 30 företag som saknar adress. Hämta /kontakt på domänen (T2) eller korsa med Bolagsverket/SCB öppna data (T0). Normalisera postnummer till formatet 'NNN NN'. Spara source_url.",
  },
  {
    id: "capture-anstallda",
    field: "antal_anstallda",
    label: "Antal anställda",
    icon: <Users className="w-3.5 h-3.5" />,
    color: "bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100",
    prompt:
      "Hämta antal anställda för 20 företag som saknar antal_anstallda. Använd T4 allabolag.se/bransch-sök (ENDAST listsidor — /foretag/* är en blockerad React-SPA). Spara antal_anstallda som mittpunkt av träfflistans range och härled storlek_kategori automatiskt (liten/medel/multinationell).",
  },
];

type DataCaptureButtonsProps = {
  onPick: (prompt: string) => void;
  disabled?: boolean;
};

export function DataCaptureButtons({ onPick, disabled }: DataCaptureButtonsProps) {
  return (
    <div className="mb-4 rounded-lg border border-emerald-100 bg-emerald-50/40 p-3">
      <div className="flex items-center gap-1.5 mb-2 text-xs font-bold uppercase tracking-wider text-emerald-700">
        <Target className="w-3.5 h-3.5" />
        Snabbfångst — ett fält i taget
      </div>
      <div className="flex flex-wrap gap-1.5">
        {DATA_CAPTURE_ACTIONS.map((a) => (
          <button
            key={a.id}
            type="button"
            onClick={() => onPick(a.prompt)}
            disabled={disabled}
            title={`Fyll i en prompt som fångar fältet "${a.field}"`}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${a.color}`}
          >
            {a.icon}
            {a.label}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-emerald-700/70 mt-2">
        Klicka för att fylla i textarea med en färdig prompt för just det fältet.
        Justera fritt innan du genererar planen.
      </p>
    </div>
  );
}
