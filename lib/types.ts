export type Address = {
  gata: string;
  postnummer: string;
  stad: string;
  region: string;
  land: string;
};

export type StorlekKategori = "liten" | "medel" | "multinationell" | "";

export type Verifieringsmetod =
  | "linkedin"
  | "foretagswebbplats"
  | "pressmeddelande"
  | "serpapi"
  | "manuell"
  | "annan"
  | "";

export type Contact = {
  id: string;
  namn: string;
  roll: string;
  telefon: string;
  email: string;
  linkedinUrl: string;
  verifierad: boolean;
  verifieringsmetod: Verifieringsmetod;
  verifieringskalla: string;
  verifieratAv: string;
  verifieratDatum: string;
};

export type Company = {
  id: string;
  schemaVersion: 2;
  organisationsnummer: string;
  domain: string;
  foretagsnamn: string;
  bolagsnamn: string;
  antalAnstallda: number | null;
  storlekKategori: StorlekKategori;
  storlekManuell: boolean;
  adress: Address;
  receptionTelefon: string;
  emailInfo: string;
  sniPrimaryKod: string;
  sniBranscher: string;
  sniHuvudgrupp: string;
  sniAllaKoder: string[];
  kontakter: Contact[];
  sokFlerKontakter: boolean;
  internaAnteckningar: string;
  arkiverad: boolean;
  arkiveradDatum: string;
  arkiveradAv: string;
  enrichedAt?: string;
  skapadDatum: string;
  senastAndrad: string;
};

export type ContactFormData = Omit<Contact, "id"> & { id?: string };

export type CompanyFormData = {
  organisationsnummer: string;
  domain: string;
  foretagsnamn: string;
  bolagsnamn: string;
  antalAnstallda: number | null;
  storlekKategori: StorlekKategori;
  storlekManuell: boolean;
  adress: Address;
  receptionTelefon: string;
  emailInfo: string;
  sniPrimaryKod?: string;
  sniBranscher?: string;
  sniHuvudgrupp?: string;
  kontakter: ContactFormData[];
  sokFlerKontakter: boolean;
  internaAnteckningar: string;
};

export type Filters = {
  region: string;
  stad: string;
  land: string;
  storlek: StorlekKategori | "";
  sniHuvudgrupp: string;
  endastVerifierade: boolean;
  endastMedDomain: boolean;
  andradFran: string;
  andradTill: string;
};

/** SNI 2007 sezione → descrizione svedese (16 sezioni). */
export const SNI_HUVUDGRUPPER: Record<string, string> = {
  A: "Jordbruk, skogsbruk och fiske",
  B: "Utvinning av mineral",
  C: "Tillverkning",
  D: "Försörjning av el, gas, värme och kyla",
  E: "Vattenförsörjning, avlopp, avfall",
  F: "Byggverksamhet",
  G: "Handel; reparation av motorfordon",
  H: "Transport och magasinering",
  I: "Hotell- och restaurangverksamhet",
  J: "Informations- och kommunikation",
  K: "Finans- och försäkringsverksamhet",
  L: "Fastighetsverksamhet",
  M: "Juridik, ekonomi, vetenskap, teknik",
  N: "Uthyrning, fastighetsservice, stödtjänster",
  O: "Offentlig förvaltning och försvar",
  P: "Utbildning",
  Q: "Vård och omsorg; sociala tjänster",
  R: "Kultur, nöje och fritid",
  S: "Annan serviceverksamhet",
  T: "Förvärvsarbete i hushåll",
  U: "Internationella organisationer",
  Z: "Vilande / Holdingbolag",
};

export type ImportResult = {
  imported: number;
  updated: number;
  skipped?: number;
  total?: number;
};

export type ToastType = "success" | "error" | "info";

export type ModalMode = "add" | "edit" | "import" | "export" | null;

export const SVERIGE_REGIONER = [
  "Blekinge län",
  "Dalarnas län",
  "Gotlands län",
  "Gävleborgs län",
  "Hallands län",
  "Jämtlands län",
  "Jönköpings län",
  "Kalmar län",
  "Kronobergs län",
  "Norrbottens län",
  "Skåne län",
  "Stockholms län",
  "Södermanlands län",
  "Uppsala län",
  "Värmlands län",
  "Västerbottens län",
  "Västernorrlands län",
  "Västmanlands län",
  "Västra Götalands län",
  "Örebro län",
  "Östergötlands län",
];

export const STORLEK_LABELS: Record<Exclude<StorlekKategori, "">, string> = {
  liten: "Liten",
  medel: "Medel",
  multinationell: "Multinationell",
};

export const STORLEK_THRESHOLDS = {
  liten: { min: 0, max: 49 },
  medel: { min: 50, max: 249 },
  multinationell: { min: 250, max: Infinity },
} as const;

export const VERIFIERINGSMETOD_LABELS: Record<
  Exclude<Verifieringsmetod, "">,
  string
> = {
  linkedin: "LinkedIn",
  foretagswebbplats: "Företagswebbplats",
  pressmeddelande: "Pressmeddelande",
  serpapi: "SERP / Sökmotor",
  manuell: "Manuell",
  annan: "Annan källa",
};

export const GENERIC_EMAIL_LOCALS = [
  "info",
  "kontakt",
  "hej",
  "hello",
  "post",
  "mail",
  "support",
  "admin",
  "sales",
  "marketing",
  "hr",
  "kundtjanst",
  "kundtjänst",
  "noreply",
  "no-reply",
  "office",
  "contact",
] as const;

export const SUSPICIOUS_PERSONAL_EMAIL_DOMAINS = [
  "gmail.com",
  "hotmail.com",
  "hotmail.se",
  "yahoo.com",
  "yahoo.se",
  "outlook.com",
  "live.se",
  "icloud.com",
  "msn.com",
] as const;
