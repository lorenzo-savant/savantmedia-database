// Common HTTP client + parsers for Brave / Bing / Ecosia SERP scraping.
//
// All exports are ESM. No external state. Used by find_emails.js and
// find_dm_email.js.

import axios from "axios";
import * as cheerio from "cheerio";

// Pool di User-Agent reali (rotated per request) — antifingerprint base.
const USER_AGENTS = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
];

const EMAIL_RE = /[a-zA-Z0-9._%+\-åäöÅÄÖ]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g;

function pickUA() {
  return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
}

async function fetchWithRetry(url, { tries = 3, timeoutMs = 15000 } = {}) {
  let lastErr = null;
  for (let i = 0; i < tries; i++) {
    try {
      const res = await axios.get(url, {
        timeout: timeoutMs,
        headers: {
          "User-Agent": pickUA(),
          "Accept":
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
          "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.7",
          "Accept-Encoding": "gzip, deflate, br",
          "Connection": "keep-alive",
        },
        validateStatus: (s) => s >= 200 && s < 400,
        maxRedirects: 5,
      });
      return res.data;
    } catch (err) {
      lastErr = err;
      const status = err?.response?.status;
      // 429/503 → exp backoff. Altri errori → ritorna subito.
      if (status === 429 || status === 503) {
        await new Promise((r) => setTimeout(r, (2 ** i) * 2000));
        continue;
      }
      // Per network errors riprova solo se non l'ultimo tentativo
      if (i < tries - 1 && !status) {
        await new Promise((r) => setTimeout(r, 1500 * (i + 1)));
        continue;
      }
      throw err;
    }
  }
  throw lastErr;
}

// ── Brave ────────────────────────────────────────────────────────────────────

export async function braveSearch(query) {
  const url = `https://search.brave.com/search?q=${encodeURIComponent(query)}&source=web`;
  const html = await fetchWithRetry(url);
  const $ = cheerio.load(html);
  const results = [];
  $('div.snippet, div[data-type="web"]').each((_, el) => {
    const $el = $(el);
    const link = $el.find("a").first();
    const href = link.attr("href");
    if (!href || !href.startsWith("http")) return;
    const title = $el.find(".title, .result-header, h3, h4").first().text().trim();
    const body =
      $el.find(".snippet-description, .snippet-content, p").first().text().trim() ||
      $el.text().slice(0, 500);
    results.push({ url: href, title, body });
  });
  return results;
}

// ── Bing ─────────────────────────────────────────────────────────────────────

export async function bingSearch(query) {
  const url = `https://www.bing.com/search?q=${encodeURIComponent(query)}&cc=se&setlang=sv-SE`;
  const html = await fetchWithRetry(url);
  const $ = cheerio.load(html);
  const results = [];
  // Bing cambia spesso markup: prova multipli selettori, prima il storico
  // poi i più moderni (ogni risultato sotto #b_results dentro un <li>).
  const candidates = [
    "li.b_algo",
    "#b_results > li",
    "ol#b_results > li",
    "[data-hveid]",
  ];
  for (const sel of candidates) {
    $(sel).each((_, el) => {
      const $el = $(el);
      const link = $el.find("h2 a, a[h]").first();
      const href = link.attr("href");
      if (!href || !href.startsWith("http")) return;
      const title = link.text().trim();
      const body =
        $el.find(".b_caption p, .b_snippet, .b_lineclamp2, .b_lineclamp3, p")
          .first().text().trim() ||
        $el.text().slice(0, 500);
      results.push({ url: href, title, body });
    });
    if (results.length > 0) break;
  }
  return results;
}

// ── Ecosia ───────────────────────────────────────────────────────────────────

export async function ecosiaSearch(query) {
  const url = `https://www.ecosia.org/search?q=${encodeURIComponent(query)}&mkt=sv-se`;
  const html = await fetchWithRetry(url);
  const $ = cheerio.load(html);
  const results = [];
  $('article[data-test-id="organic-result"], article.result').each((_, el) => {
    const $el = $(el);
    const link =
      $el.find('a[data-test-id="result-link"]').first().attr("href") ||
      $el.find("a").first().attr("href");
    if (!link || !link.startsWith("http")) return;
    const title = $el.find('a[data-test-id="result-link"]').first().text().trim() ||
                  $el.find("a").first().text().trim();
    const body =
      $el.find('p[data-test-id="result-description"], p').first().text().trim() ||
      $el.text().slice(0, 500);
    results.push({ url: link, title, body });
  });
  return results;
}

// ── Email extraction ─────────────────────────────────────────────────────────

export function extractEmailsForDomain(text, domain) {
  if (!text || !domain) return new Set();
  domain = domain.toLowerCase().replace(/^\.+/, "");
  const found = new Set();
  for (const match of text.matchAll(EMAIL_RE)) {
    const em = match[0].toLowerCase();
    const host = em.split("@")[1] || "";
    if (host === domain || host.endsWith("." + domain)) {
      found.add(em);
    }
  }
  return found;
}

export function normalize(name) {
  return (name || "")
    .toLowerCase()
    .replace(/å/g, "a")
    .replace(/ä/g, "a")
    .replace(/ö/g, "o")
    .replace(/é/g, "e")
    .replace(/ü/g, "u");
}

// ── Multi-engine driver ──────────────────────────────────────────────────────

/**
 * Run query on all engines in parallel, merge email matches for `domain`.
 * Returns Set of unique emails (lowercased) on that domain.
 */
export async function searchAllEngines(query, domain) {
  const engines = [
    { name: "brave", fn: braveSearch },
    { name: "ecosia", fn: ecosiaSearch },
    { name: "bing", fn: bingSearch },
  ];
  const settled = await Promise.allSettled(
    engines.map((e) => e.fn(query))
  );

  const allEmails = new Set();
  for (const r of settled) {
    if (r.status !== "fulfilled") continue;
    for (const item of r.value) {
      const bag = [item.title, item.body, item.url].filter(Boolean).join(" ");
      for (const em of extractEmailsForDomain(bag, domain)) {
        allEmails.add(em);
      }
    }
  }
  return allEmails;
}
