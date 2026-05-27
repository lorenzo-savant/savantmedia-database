import {
  GENERIC_EMAIL_LOCALS,
  STORLEK_THRESHOLDS,
  SUSPICIOUS_PERSONAL_EMAIL_DOMAINS,
  type StorlekKategori,
} from "./types";

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("sv-SE", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDateShort(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("sv-SE", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
  });
}

export function cn(...classes: (string | boolean | undefined | null)[]): string {
  return classes.filter(Boolean).join(" ");
}

// ── Organisationsnummer (Swedish org.nr: 10 digits, formatted XXXXXX-XXXX) ──

export function normalizeOrgnr(raw: string): string {
  const digits = (raw || "").replace(/\D/g, "");
  if (digits.length !== 10) return digits;
  return `${digits.slice(0, 6)}-${digits.slice(6)}`;
}

export function isValidOrgnr(raw: string): boolean {
  const digits = (raw || "").replace(/\D/g, "");
  return digits.length === 10;
}

// ── Domain normalization ──

export function normalizeDomain(raw: string): string {
  if (!raw) return "";
  let d = raw.trim().toLowerCase();
  d = d.replace(/^https?:\/\//, "");
  d = d.replace(/^www\./, "");
  d = d.split("/")[0];
  d = d.split("?")[0];
  return d;
}

export function isValidDomain(raw: string): boolean {
  const d = normalizeDomain(raw);
  if (!d) return false;
  return /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(d);
}

// ── Storlek classification ──

export function classifyStorlek(antal: number | null): StorlekKategori {
  if (antal == null || isNaN(antal) || antal < 0) return "";
  if (antal <= STORLEK_THRESHOLDS.liten.max) return "liten";
  if (antal <= STORLEK_THRESHOLDS.medel.max) return "medel";
  return "multinationell";
}

// ── Email validation ──

export type EmailCheck = {
  valid: boolean;
  generic: boolean;
  suspiciousProvider: boolean;
  domainMismatch: boolean;
  reason: string;
};

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function checkEmail(email: string, companyDomain: string): EmailCheck {
  const result: EmailCheck = {
    valid: false,
    generic: false,
    suspiciousProvider: false,
    domainMismatch: false,
    reason: "",
  };

  const e = (email || "").trim().toLowerCase();
  if (!e) {
    result.reason = "Tom e-postadress";
    return result;
  }
  if (!EMAIL_REGEX.test(e)) {
    result.reason = "Ogiltigt format";
    return result;
  }

  const [local, domain] = e.split("@");

  if ((GENERIC_EMAIL_LOCALS as readonly string[]).includes(local)) {
    result.generic = true;
    result.reason = `Generisk adress (${local}@) — inte en personlig kontakt`;
    return result;
  }

  if ((SUSPICIOUS_PERSONAL_EMAIL_DOMAINS as readonly string[]).includes(domain)) {
    result.suspiciousProvider = true;
  }

  const expectedDomain = normalizeDomain(companyDomain);
  if (expectedDomain && domain !== expectedDomain) {
    if (!domain.endsWith("." + expectedDomain) && domain !== expectedDomain) {
      result.domainMismatch = true;
    }
  }

  result.valid = true;
  if (result.suspiciousProvider) {
    result.reason = `Personlig domän (${domain}) — verifiera att det är professionell e-post`;
  } else if (result.domainMismatch) {
    result.reason = `Domänen (${domain}) matchar inte företagets domän (${expectedDomain})`;
  }
  return result;
}

// ── LinkedIn URL normalization ──

export function normalizeLinkedinUrl(raw: string): string {
  if (!raw) return "";
  let url = raw.trim();
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  return url;
}

export function isLikelyPersonalLinkedin(raw: string): boolean {
  if (!raw) return false;
  return /linkedin\.com\/in\//i.test(raw);
}
