"use server";

import { getSupabaseAdmin } from "@/lib/supabase/server";
import type { Database } from "@/lib/database.types";

// ─────────────────────────────────────────────────────────────────────────────
// Types — esportati per il page Client Component
// ─────────────────────────────────────────────────────────────────────────────

type PlanRowDb = Database["public"]["Tables"]["plans"]["Row"];
type PlanStatusDb = Database["public"]["Enums"]["plan_status"];

/**
 * Un singolo step proposto dal nodo PLAN dell'agente.
 * Shape definito in `backend/agent/state.py` (AgentState.plan_steps).
 */
export type PlanStep = {
  id: string; // short uid, e.g. "s1"
  query: string;
  source: string; // "bolagsverket" | "scb" | "vault" | "web" | ...
  tier: number; // 0..5 (T0 free open data → T5 paid premium)
  expected_yield: string;
  rationale: string;
};

/**
 * Row reduced per le liste compatte ("Senaste planer").
 */
export type PlanRow = {
  id: string;
  user_prompt: string;
  status: PlanStatusDb;
  created_at: string;
  step_count: number;
  approved_count: number;
};

/**
 * Plan completo con tutti gli step + approvazioni.
 */
export type PlanFull = {
  id: string;
  user_prompt: string;
  status: PlanStatusDb;
  created_at: string;
  approved_at: string | null;
  steps: PlanStep[];
  approved_step_ids: string[];
  /**
   * True dopo che `/approve` ha schedulato il task di esecuzione in background.
   * Solo restituito dal FastAPI agent — il fallback DB write lo lascia false.
   */
  executing?: boolean;
};

/**
 * Single scrape_jobs row, shape allineata a backend `ScrapeJobRow`.
 */
export type ScrapeJobRow = {
  id: string;
  plan_id: string | null;
  query: string | null;
  target_domain: string | null;
  tier_used: number | null;
  status: "pending" | "running" | "done" | "blocked" | "failed";
  result_count: number | null;
  blocked_reason: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
};

/**
 * Live execution view returned by `GET /orchestrator/plans/{id}/execution`.
 */
export type PlanExecutionView = {
  planId: string;
  jobs: ScrapeJobRow[];
  counts: Record<string, number>;
  /** True quando almeno un job è ancora `pending` o `running`. */
  isActive: boolean;
};

// ─────────────────────────────────────────────────────────────────────────────
// FastAPI backend config
// ─────────────────────────────────────────────────────────────────────────────

const BACKEND_URL =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL || "http://localhost:8000";
const BACKEND_TOKEN = process.env.ORCHESTRATOR_API_TOKEN || "";

function backendHeaders(): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (BACKEND_TOKEN) h["Authorization"] = `Bearer ${BACKEND_TOKEN}`;
  return h;
}

/**
 * True if the error looks like "backend agent FastAPI non in piedi".
 * We catch ECONNREFUSED, fetch failures and AbortError (timeout).
 */
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

/**
 * Fetch wrapper con timeout corto (4s) — il backend è in locale.
 * Throws normally on HTTP errors; the caller decides whether to fallback.
 */
async function backendFetch(
  path: string,
  init: RequestInit = {},
  timeoutMs = 4000
): Promise<Response> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      headers: { ...backendHeaders(), ...(init.headers || {}) },
      signal: ctrl.signal,
      cache: "no-store",
    });
    return res;
  } finally {
    clearTimeout(t);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// JSON helpers — `steps` colonna è jsonb, quindi torna `Json` dal generated type
// ─────────────────────────────────────────────────────────────────────────────

function coerceSteps(raw: unknown): PlanStep[] {
  if (!Array.isArray(raw)) return [];
  const out: PlanStep[] = [];
  for (const item of raw) {
    if (item && typeof item === "object") {
      const s = item as Record<string, unknown>;
      out.push({
        id: String(s.id ?? ""),
        query: String(s.query ?? ""),
        source: String(s.source ?? ""),
        tier: typeof s.tier === "number" ? s.tier : Number(s.tier ?? 0) || 0,
        expected_yield: String(s.expected_yield ?? ""),
        rationale: String(s.rationale ?? ""),
      });
    }
  }
  return out;
}

function coerceApprovedIds(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((x) => typeof x === "string") as string[];
}

function rowToFull(r: PlanRowDb): PlanFull {
  return {
    id: r.id,
    user_prompt: r.user_prompt,
    status: r.status,
    created_at: r.created_at,
    approved_at: r.approved_at,
    steps: coerceSteps(r.steps),
    approved_step_ids: coerceApprovedIds(r.approved_steps),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Sample plan — fallback quando il FastAPI agent non è ancora in piedi
// ─────────────────────────────────────────────────────────────────────────────

function sampleStepsForPrompt(prompt: string): PlanStep[] {
  // Plan "didattico" che mostra tutti i tier, così la UI può essere testata
  // anche senza backend Python in esecuzione.
  return [
    {
      id: "s1",
      query: `Sök i public.companies efter företag relaterade till: "${prompt.slice(0, 80)}"`,
      source: "vault",
      tier: 0,
      expected_yield: "Träffar redan i databasen (dedupering innan vi scrapar)",
      rationale: "RECALL-steg: kolla först vad vi redan har, undvik dubbelarbete.",
    },
    {
      id: "s2",
      query: "Hämta organisationsdata från Bolagsverket Öppna data",
      source: "bolagsverket",
      tier: 0,
      expected_yield: "Bolagsnamn, säte, SNI-kod, status, styrelsemedlemmar",
      rationale: "CC-BY 4.0 sedan feb 2025. Gratis, säker källa.",
    },
    {
      id: "s3",
      query: "Komplettera med SCB-statistik (antal anställda, omsättningsklass)",
      source: "scb",
      tier: 1,
      expected_yield: "Storleksklass och bransch enligt SCB",
      rationale: "Behövs för att klassificera storlek (liten/medel/multinationell).",
    },
    {
      id: "s4",
      query: "Skrapa företagets egen webbplats efter team/ledning",
      source: "web",
      tier: 2,
      expected_yield: "Namn + roller på ledningsgruppen",
      rationale: "Källan med högst signal för verifierad kontakt-information.",
    },
    {
      id: "s5",
      query: "Sökning på LinkedIn via SerpAPI för decision makers",
      source: "serpapi",
      tier: 4,
      expected_yield: "Aktuella titlar och LinkedIn-profiler",
      rationale: "Betald källa — endast om webbplatsen inte räckte.",
    },
  ];
}

// ─────────────────────────────────────────────────────────────────────────────
// Server Action: requestPlan
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Chiama il FastAPI agent per generare un piano draft.
 * Se il backend non è raggiungibile (ECONNREFUSED), fa fallback:
 * inserisce direttamente un piano di esempio in `public.plans` e lo restituisce.
 *
 * Fallback mode — backend agent not yet running.
 */
export async function requestPlan(
  prompt: string
): Promise<{ planId: string; steps: PlanStep[]; threadId: string | null }> {
  const trimmed = (prompt || "").trim();
  if (!trimmed) {
    throw new Error("Prompt får inte vara tomt.");
  }

  // 1) Try FastAPI agent first
  try {
    const res = await backendFetch("/orchestrator/plan", {
      method: "POST",
      body: JSON.stringify({ user_prompt: trimmed }),
    });

    if (res.ok) {
      const data = (await res.json()) as {
        plan_id?: string;
        thread_id?: string;
        steps?: unknown;
      };
      if (data.plan_id) {
        return {
          planId: data.plan_id,
          steps: coerceSteps(data.steps),
          threadId: data.thread_id ?? null,
        };
      }
      // Backend reachable but malformed response → fallback
      console.warn("Orchestrator: malformed FastAPI response, fallback to direct DB insert.");
    } else if (res.status >= 500) {
      // 5xx → fallback
      console.warn(`Orchestrator: FastAPI ${res.status}, fallback to direct DB insert.`);
    } else {
      // 4xx → propaga (es. validation error dal backend)
      const text = await res.text();
      throw new Error(`Backend rejected plan request (${res.status}): ${text}`);
    }
  } catch (err) {
    if (!isBackendUnreachable(err)) {
      // Errore reale, non un "non in piedi" — rilancia
      throw err;
    }
    // Fallthrough → fallback path
  }

  // 2) Fallback — insert sample plan direttamente in Supabase
  // Fallback mode — backend agent not yet running.
  const sb = getSupabaseAdmin();
  const steps = sampleStepsForPrompt(trimmed);
  const { data, error } = await sb
    .from("plans")
    .insert({
      user_prompt: trimmed,
      steps: steps as unknown as Database["public"]["Tables"]["plans"]["Insert"]["steps"],
      status: "draft",
    })
    .select("id")
    .single();
  if (error) throw error;
  return { planId: data.id, steps, threadId: null };
}

// ─────────────────────────────────────────────────────────────────────────────
// Server Action: getRecentPlans
// ─────────────────────────────────────────────────────────────────────────────

export async function getRecentPlans(limit = 10): Promise<PlanRow[]> {
  const sb = getSupabaseAdmin();
  const { data, error } = await sb
    .from("plans")
    .select("id, user_prompt, status, created_at, steps, approved_steps")
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) throw error;

  return (data ?? []).map((r) => {
    const steps = coerceSteps(r.steps);
    const approved = coerceApprovedIds(r.approved_steps);
    return {
      id: r.id,
      user_prompt: r.user_prompt,
      status: r.status,
      created_at: r.created_at,
      step_count: steps.length,
      approved_count: approved.length,
    };
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Server Action: getPlanById
// ─────────────────────────────────────────────────────────────────────────────

export async function getPlanById(id: string): Promise<PlanFull | null> {
  // 1) Prova prima il FastAPI agent (potrebbe avere stato in-memory più aggiornato)
  try {
    const res = await backendFetch(`/orchestrator/plans/${encodeURIComponent(id)}`);
    if (res.ok) {
      const data = (await res.json()) as Record<string, unknown>;
      if (data && data.id) {
        return {
          id: String(data.id),
          user_prompt: String(data.user_prompt ?? ""),
          status: (data.status as PlanStatusDb) ?? "draft",
          created_at: String(data.created_at ?? new Date().toISOString()),
          approved_at: (data.approved_at as string | null) ?? null,
          steps: coerceSteps(data.steps),
          approved_step_ids: coerceApprovedIds(data.approved_step_ids ?? data.approved_steps),
        };
      }
    }
  } catch (err) {
    if (!isBackendUnreachable(err)) {
      // Errore reale non-rete → cade in Supabase, ma logga
      console.warn("Orchestrator getPlanById: backend error, falling back to DB:", err);
    }
  }

  // 2) Fallback diretto a Supabase
  const sb = getSupabaseAdmin();
  const { data, error } = await sb
    .from("plans")
    .select("*")
    .eq("id", id)
    .maybeSingle();
  if (error) throw error;
  if (!data) return null;
  return rowToFull(data);
}

// ─────────────────────────────────────────────────────────────────────────────
// Server Action: approvePlan
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Approva un sottoinsieme di step. Tenta FastAPI (che resume il LangGraph
 * dall'interrupt `wait_approval` e schedula l'EXECUTE chain in background);
 * se non raggiungibile, scrive direttamente in `public.plans`.
 *
 * @returns `{ executing: true }` se il backend ha schedulato l'esecuzione,
 *          `{ executing: false }` se è stato usato il fallback DB-write.
 */
export async function approvePlan(
  id: string,
  approvedStepIds: string[],
  threadId?: string | null
): Promise<{ executing: boolean }> {
  const cleanIds = Array.from(new Set(approvedStepIds.filter(Boolean)));

  // 1) FastAPI first
  try {
    const res = await backendFetch(`/orchestrator/plans/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      body: JSON.stringify({
        approved_step_ids: cleanIds,
        thread_id: threadId ?? null,
      }),
    });
    if (res.ok) {
      const data = (await res.json()) as { executing?: boolean };
      return { executing: Boolean(data.executing) };
    }
    if (res.status < 500) {
      const text = await res.text();
      throw new Error(`Backend rejected approval (${res.status}): ${text}`);
    }
    console.warn(`Orchestrator approve: FastAPI ${res.status}, fallback to direct DB write.`);
  } catch (err) {
    if (!isBackendUnreachable(err)) throw err;
  }

  // 2) Fallback — scrive direttamente in Supabase
  const sb = getSupabaseAdmin();
  const { error } = await sb
    .from("plans")
    .update({
      approved_steps:
        cleanIds as unknown as Database["public"]["Tables"]["plans"]["Update"]["approved_steps"],
      status: "approved",
      approved_at: new Date().toISOString(),
    })
    .eq("id", id);
  if (error) throw error;
  return { executing: false };
}

// ─────────────────────────────────────────────────────────────────────────────
// Server Action: getPlanExecution
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Polla lo stato vivo di `scrape_jobs` per un piano. Usata dalla cockpit
 * UI mentre l'EXECUTE chain gira in background.
 *
 * 1. Prova prima il FastAPI agent (`GET /orchestrator/plans/{id}/execution`)
 *    che restituisce anche un eventuale state-snapshot della LangGraph.
 * 2. Fallback: SELECT diretto su `public.scrape_jobs`.
 */
export async function getPlanExecution(
  id: string,
  threadId?: string | null
): Promise<PlanExecutionView> {
  // 1) Try FastAPI agent first
  try {
    const qs = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : "";
    const res = await backendFetch(
      `/orchestrator/plans/${encodeURIComponent(id)}/execution${qs}`
    );
    if (res.ok) {
      const data = (await res.json()) as {
        plan_id?: string;
        jobs?: unknown;
        counts?: Record<string, number>;
      };
      const jobs = coerceJobs(data.jobs);
      const counts = data.counts ?? countByStatus(jobs);
      return {
        planId: String(data.plan_id ?? id),
        jobs,
        counts,
        isActive: (counts.pending ?? 0) + (counts.running ?? 0) > 0,
      };
    }
  } catch (err) {
    if (!isBackendUnreachable(err)) {
      console.warn("Orchestrator getPlanExecution: backend error, falling back to DB:", err);
    }
  }

  // 2) Fallback — diretto a Supabase
  const sb = getSupabaseAdmin();
  const { data, error } = await sb
    .from("scrape_jobs")
    .select(
      "id, plan_id, query, target_domain, tier_used, status, result_count, blocked_reason, error_message, started_at, finished_at, created_at"
    )
    .eq("plan_id", id)
    .order("created_at", { ascending: true });
  if (error) throw error;

  const jobs = coerceJobs(data ?? []);
  const counts = countByStatus(jobs);
  return {
    planId: id,
    jobs,
    counts,
    isActive: (counts.pending ?? 0) + (counts.running ?? 0) > 0,
  };
}

function coerceJobs(raw: unknown): ScrapeJobRow[] {
  if (!Array.isArray(raw)) return [];
  const out: ScrapeJobRow[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;
    out.push({
      id: String(r.id ?? ""),
      plan_id: (r.plan_id as string | null) ?? null,
      query: (r.query as string | null) ?? null,
      target_domain: (r.target_domain as string | null) ?? null,
      tier_used:
        typeof r.tier_used === "number" ? r.tier_used : r.tier_used == null ? null : Number(r.tier_used),
      status: ((r.status as string) ?? "pending") as ScrapeJobRow["status"],
      result_count:
        typeof r.result_count === "number"
          ? r.result_count
          : r.result_count == null
          ? null
          : Number(r.result_count),
      blocked_reason: (r.blocked_reason as string | null) ?? null,
      error_message: (r.error_message as string | null) ?? null,
      started_at: (r.started_at as string | null) ?? null,
      finished_at: (r.finished_at as string | null) ?? null,
      created_at: (r.created_at as string | null) ?? null,
    });
  }
  return out;
}

function countByStatus(jobs: ScrapeJobRow[]): Record<string, number> {
  const counts: Record<string, number> = {
    pending: 0,
    running: 0,
    done: 0,
    blocked: 0,
    failed: 0,
  };
  for (const j of jobs) {
    counts[j.status] = (counts[j.status] ?? 0) + 1;
  }
  return counts;
}
