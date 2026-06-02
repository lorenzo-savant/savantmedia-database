// CLI: trova email di una persona specifica su un dominio.
// Combina query mirate + scoring per nome match nel local-part.
//
// Usage:
//   node find_dm_email.js "Erik Andersson" savantmedia.se
//
// Stdout: JSON array di email match, ordinate per score descrescente.

import { searchAllEngines, normalize } from "./common.js";

async function main() {
  const personName = process.argv[2];
  const domain = (process.argv[3] || "").trim().toLowerCase().replace(/^\.+/, "");
  if (!personName || !domain) {
    console.error('Usage: node find_dm_email.js "Firstname Lastname" <domain>');
    process.exit(1);
  }

  const queries = [
    `"${personName}" "@${domain}"`,
    `"${personName}" "${domain}" email`,
    `site:${domain} "${personName}"`,
  ];

  const all = new Set();
  for (const q of queries) {
    try {
      const set = await searchAllEngines(q, domain);
      for (const em of set) all.add(em);
      console.error(`query "${q}" → ${set.size} hits`);
    } catch (err) {
      console.error(`query "${q}" → ERROR: ${err?.message || err}`);
    }
  }

  // Score by name match in email local-part
  const normName = normalize(personName);
  const parts = normName.split(/\s+/).filter((p) => p.length >= 2);
  const first = parts[0] || "";
  const last = parts[parts.length - 1] || "";

  const scored = [];
  for (const em of all) {
    const local = normalize(em.split("@")[0]);
    let score = 0;
    if (last && local.includes(last)) score += 4;
    if (first && local.includes(first)) score += 2;
    if (last && first) {
      if (local.includes(`${first}.${last}`)) score += 3;
      if (local.includes(`${first[0]}.${last}`)) score += 1;
    }
    if (score > 0) scored.push({ score, em });
  }
  scored.sort((a, b) => b.score - a.score);

  console.log(JSON.stringify(scored.map((s) => s.em)));
}

main().catch((err) => {
  console.error("FATAL:", err?.stack || err);
  process.exit(1);
});
