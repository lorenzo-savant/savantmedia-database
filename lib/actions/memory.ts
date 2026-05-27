"use server";

import { getSupabaseAdmin } from "@/lib/supabase/server";
import type { Database, Json } from "@/lib/database.types";

// ─────────────────────────────────────────────────────────────────────────────
// Types — esportati per il page Client Component
// ─────────────────────────────────────────────────────────────────────────────

export type KnowledgeKind = Database["public"]["Enums"]["knowledge_kind"];

/**
 * Chunk di memoria dell'agente — sotto-insieme di colonne di public.knowledge_chunks
 * sufficiente per la UI di browse / search (NB: `embedding` non viene letto).
 *
 * `audit_note` è opzionale, popolato solo quando un fallback è avvenuto
 * (es. FastAPI non raggiungibile → ricerca semantica degrada a keyword).
 */
export type MemoryChunk = {
  id: string;
  kind: KnowledgeKind;
  content: string;
  metadata: Json;
  vault_path: string | null;
  source_url: string | null;
  created_at: string;
  audit_note?: string;
};

export type KnowledgeStats = {
  total: number;
  by_kind: Record<KnowledgeKind, number>;
};

export type SearchKnowledgeArgs = {
  kind?: KnowledgeKind | null;
  query?: string | null;
  sourceFilter?: string | null;
  limit?: number;
  offset?: number;
};

export type SemanticSearchArgs = {
  query: string;
  kind?: KnowledgeKind | null;
  limit?: number;
};

// ─────────────────────────────────────────────────────────────────────────────
// FastAPI backend config (per ricerca semantica via Ollama)
// ─────────────────────────────────────────────────────────────────────────────

const BACKEND_URL =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL || "http://localhost:8000";
const BACKEND_TOKEN = process.env.ORCHESTRATOR_API_TOKEN || "";

function backendHeaders(): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (BACKEND_TOKEN) h["Authorization"] = `Bearer ${BACKEND_TOKEN}`;
  return h;
}

function isBackendUnreachable(err: unknown): boolean {
  if (!err) return false;
  const msg = err instanceof Error ? err.message : String(err);
  return (
    /ECONNREFUSED/i.test(msg) ||
    /fetch failed/i.test(msg) ||
    /network/i.test(msg) ||
    /timeout/i.test(msg) ||
    /aborted/i.test(msg) ||
    /ENOTFOUND/i.test(msg)
  );
}

async function backendFetch(
  path: string,
  init: RequestInit = {},
  timeoutMs = 8000
): Promise<Response> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      headers: { ...backendHeaders(), ...(init.headers || {}) },
      signal: ctrl.signal,
      cache: "no-store",
    });
  } finally {
    clearTimeout(t);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

type SelectedKnowledgeRow = Pick<
  Database["public"]["Tables"]["knowledge_chunks"]["Row"],
  "id" | "kind" | "content" | "metadata" | "vault_path" | "source_url" | "created_at"
>;

/**
 * Escape % e _ nei pattern ILIKE, e i caratteri , / . che farebbero
 * incasinare il PostgREST `.or()` filter syntax.
 */
function sanitizeIlikePattern(raw: string): string {
  // ILIKE escape
  let s = raw.replace(/\\/g, "\\\\").replace(/%/g, "\\%").replace(/_/g, "\\_");
  // PostgREST `.or()` parser usa la virgola come separatore — la rimuoviamo
  // (ricerche su comma non sono comuni nei path di vault / URL).
  s = s.replace(/,/g, " ");
  return s;
}

function rowToChunk(r: SelectedKnowledgeRow): MemoryChunk {
  return {
    id: r.id,
    kind: r.kind,
    content: r.content,
    metadata: r.metadata,
    vault_path: r.vault_path,
    source_url: r.source_url,
    created_at: r.created_at,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Server Action: searchKnowledgeChunks (keyword / browse mode)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Read diretto da public.knowledge_chunks via Supabase service role.
 * Senza embeddings, niente Ollama. Filtra per kind, ricerca substring su content,
 * e filtra source via vault_path / source_url.
 */
export async function searchKnowledgeChunks(
  args: SearchKnowledgeArgs = {}
): Promise<MemoryChunk[]> {
  const { kind, query, sourceFilter } = args;
  const limit = Math.min(Math.max(args.limit ?? 20, 1), 100);
  const offset = Math.max(args.offset ?? 0, 0);

  const sb = getSupabaseAdmin();
  let q = sb
    .from("knowledge_chunks")
    .select("id, kind, content, metadata, vault_path, source_url, created_at")
    .order("created_at", { ascending: false });

  if (kind) {
    q = q.eq("kind", kind);
  }

  const trimmedQuery = (query ?? "").trim();
  if (trimmedQuery) {
    q = q.ilike("content", `%${sanitizeIlikePattern(trimmedQuery)}%`);
  }

  const trimmedSource = (sourceFilter ?? "").trim();
  if (trimmedSource) {
    const p = sanitizeIlikePattern(trimmedSource);
    q = q.or(`vault_path.ilike.%${p}%,source_url.ilike.%${p}%`);
  }

  q = q.range(offset, offset + limit - 1);

  const { data, error } = await q;
  if (error) throw error;
  return (data ?? []).map(rowToChunk);
}

// ─────────────────────────────────────────────────────────────────────────────
// Server Action: semanticSearchKnowledgeChunks
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Chiama FastAPI POST /orchestrator/memory/search che fa l'embedding via Ollama
 * e ritorna i top-N chunk per cosine similarity.
 *
 * Se FastAPI non è raggiungibile, fa fallback su searchKnowledgeChunks (keyword)
 * e marca ogni risultato con `audit_note` così la UI può mostrare un banner.
 */
export async function semanticSearchKnowledgeChunks(
  args: SemanticSearchArgs
): Promise<MemoryChunk[]> {
  const query = (args.query ?? "").trim();
  if (!query) return [];
  const limit = Math.min(Math.max(args.limit ?? 20, 1), 100);

  try {
    const res = await backendFetch("/orchestrator/memory/search", {
      method: "POST",
      body: JSON.stringify({ query, kind: args.kind ?? null, limit }),
    });

    if (res.ok) {
      const data = (await res.json()) as { results?: unknown };
      if (Array.isArray(data.results)) {
        return data.results.map((raw) => coerceRemoteChunk(raw));
      }
      console.warn(
        "Memory: malformed FastAPI response, fallback to keyword search."
      );
    } else if (res.status >= 500) {
      console.warn(
        `Memory: FastAPI ${res.status} on semantic search, fallback to keyword.`
      );
    } else {
      const text = await res.text();
      throw new Error(`Backend rejected semantic search (${res.status}): ${text}`);
    }
  } catch (err) {
    if (!isBackendUnreachable(err)) {
      // Errore reale, non un "non in piedi" — rilancia
      throw err;
    }
    // fall through to keyword fallback
  }

  // Fallback — keyword search con nota di audit
  const fallback = await searchKnowledgeChunks({
    kind: args.kind ?? null,
    query,
    limit,
    offset: 0,
  });
  const note =
    "Semantisk sökning ej tillgänglig (FastAPI/Ollama nere) — fallback till nyckelordssökning.";
  return fallback.map((c) => ({ ...c, audit_note: note }));
}

function coerceRemoteChunk(raw: unknown): MemoryChunk {
  const r = (raw ?? {}) as Record<string, unknown>;
  const kindRaw = r.kind;
  const validKinds: KnowledgeKind[] = [
    "playbook",
    "snippet",
    "query_log",
    "lesson",
  ];
  const kind: KnowledgeKind = validKinds.includes(kindRaw as KnowledgeKind)
    ? (kindRaw as KnowledgeKind)
    : "snippet";

  return {
    id: String(r.id ?? ""),
    kind,
    content: String(r.content ?? ""),
    metadata: (r.metadata as Json) ?? {},
    vault_path: (r.vault_path as string | null) ?? null,
    source_url: (r.source_url as string | null) ?? null,
    created_at: String(r.created_at ?? new Date().toISOString()),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Server Action: getKnowledgeStats
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Conta totale e per-kind. Usa una read "magra" di solo `kind` (no embedding /
 * no content) e aggrega in TS — la tabella è piccola, quindi va bene per ora.
 *
 * NB: PostgREST non ha COUNT GROUP BY built-in senza una RPC. Per il volume
 * atteso (< qualche migliaio di righe), il client-side aggregate è ok.
 */
export async function getKnowledgeStats(): Promise<KnowledgeStats> {
  const sb = getSupabaseAdmin();
  const { data, error } = await sb.from("knowledge_chunks").select("kind");
  if (error) throw error;

  const by_kind: Record<KnowledgeKind, number> = {
    playbook: 0,
    snippet: 0,
    query_log: 0,
    lesson: 0,
  };
  for (const row of data ?? []) {
    const k = row.kind as KnowledgeKind;
    if (k in by_kind) by_kind[k] += 1;
  }
  return {
    total: data?.length ?? 0,
    by_kind,
  };
}
