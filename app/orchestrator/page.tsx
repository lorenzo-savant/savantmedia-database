"use client";

import { useEffect, useState, useTransition, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronLeft,
  Lightbulb,
  Send,
  CheckCircle2,
  Sparkles,
  History,
  ListChecks,
  RefreshCcw,
  AlertCircle,
  Layers,
  Target,
  FileText,
  Brain,
  PlayCircle,
  Loader2,
  XCircle,
  ShieldAlert,
  Clock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { SavantLogo } from "@/components/savant-logo";
import { PromptSuggestions } from "./_components/prompt-suggestions";
import { DataCaptureButtons } from "./_components/data-capture-buttons";
import { formatDate } from "@/lib/utils";
import {
  requestPlan,
  getRecentPlans,
  getPlanById,
  approvePlan,
  getPlanExecution,
  type PlanStep,
  type PlanRow,
  type PlanFull,
  type ScrapeJobRow,
  type PlanExecutionView,
} from "@/lib/actions/orchestrator";

// ─────────────────────────────────────────────────────────────────────────────
// Tier styling — T0=emerald (free open data) … T5=red (premium paid)
// ─────────────────────────────────────────────────────────────────────────────

const TIER_STYLES: Record<number, { badge: string; label: string }> = {
  0: {
    badge: "bg-emerald-50 text-emerald-700 border-emerald-200",
    label: "T0 — Öppna data",
  },
  1: {
    badge: "bg-blue-50 text-blue-700 border-blue-200",
    label: "T1 — Billig API",
  },
  2: {
    badge: "bg-indigo-50 text-indigo-700 border-indigo-200",
    label: "T2 — Skrapning",
  },
  3: {
    badge: "bg-violet-50 text-violet-700 border-violet-200",
    label: "T3 — Avancerad skrapning",
  },
  4: {
    badge: "bg-amber-50 text-amber-700 border-amber-200",
    label: "T4 — Betald API",
  },
  5: {
    badge: "bg-red-50 text-red-700 border-red-200",
    label: "T5 — Premium",
  },
};

function tierStyle(tier: number) {
  return TIER_STYLES[tier] ?? TIER_STYLES[0];
}

// ─────────────────────────────────────────────────────────────────────────────
// Status badge
// ─────────────────────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<string, string> = {
  draft: "bg-amber-50 text-amber-700 border-amber-200",
  approved: "bg-emerald-50 text-emerald-700 border-emerald-200",
  executing: "bg-blue-50 text-blue-700 border-blue-200",
  done: "bg-gray-100 text-gray-700 border-gray-300",
  cancelled: "bg-red-50 text-red-700 border-red-200",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "Utkast",
  approved: "Godkänd",
  executing: "Kör",
  done: "Klar",
  cancelled: "Avbruten",
};

function StatusBadge({ status }: { status: string }) {
  const styles = STATUS_STYLES[status] ?? STATUS_STYLES.draft;
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold border ${styles}`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Tabs
// ─────────────────────────────────────────────────────────────────────────────

type TabId = "new" | "active" | "history";

const TABS: { id: TabId; label: string; icon: typeof Lightbulb }[] = [
  { id: "new", label: "Nytt plan", icon: Lightbulb },
  { id: "active", label: "Aktiv plan", icon: ListChecks },
  { id: "history", label: "Senaste planer", icon: History },
];

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function OrchestratorPage() {
  const router = useRouter();
  const { showToast } = useToast();
  const [tab, setTab] = useState<TabId>("new");

  // Form state
  const [prompt, setPrompt] = useState("");
  const [pendingPlan, startPlanTransition] = useTransition();

  // Active plan state
  const [activePlan, setActivePlan] = useState<PlanFull | null>(null);
  const [approvedIds, setApprovedIds] = useState<Set<string>>(new Set());
  const [pendingApprove, startApproveTransition] = useTransition();
  const [activeLoading, setActiveLoading] = useState(false);
  /** Thread id returned by `requestPlan` — needed to resume LangGraph on approve. */
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);

  // Live execution polling state (Fase 12)
  const [execution, setExecution] = useState<PlanExecutionView | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // History state
  const [history, setHistory] = useState<PlanRow[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  // ── Load history ──────────────────────────────────────────────────────────
  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const rows = await getRecentPlans(10);
      setHistory(rows);
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : String(err));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  // ── Load a specific plan into "active" tab ────────────────────────────────
  const loadPlanIntoActive = useCallback(
    async (id: string) => {
      setActiveLoading(true);
      try {
        const full = await getPlanById(id);
        if (!full) {
          showToast("Planen hittades inte.", "error");
          return;
        }
        setActivePlan(full);
        // History-loaded plans don't carry the live thread_id — the LangGraph
        // checkpoint may also be gone (MemorySaver = process-local). The UI
        // will still allow re-approve, but the EXECUTE chain won't auto-run.
        setActiveThreadId(null);
        // Default: gli step già godkända restano markerade; altrimenti tutti.
        const initial =
          full.approved_step_ids.length > 0
            ? new Set(full.approved_step_ids)
            : new Set(full.steps.map((s) => s.id));
        setApprovedIds(initial);
        // Reset execution view, then pre-load if the plan is already approved/executing/done.
        setExecution(null);
        setExecutionError(null);
        setIsExecuting(
          full.status === "approved" ||
            full.status === "executing"
        );
        setTab("active");
      } catch (err) {
        showToast(
          err instanceof Error ? err.message : "Kunde inte hämta planen",
          "error"
        );
      } finally {
        setActiveLoading(false);
      }
    },
    [showToast]
  );

  // ── Generate new plan ─────────────────────────────────────────────────────
  const handleGenerate = () => {
    const p = prompt.trim();
    if (!p) {
      showToast("Skriv en prompt först.", "info");
      return;
    }
    startPlanTransition(async () => {
      try {
        const { planId, steps, threadId } = await requestPlan(p);
        const optimistic: PlanFull = {
          id: planId,
          user_prompt: p,
          status: "draft",
          created_at: new Date().toISOString(),
          approved_at: null,
          steps,
          approved_step_ids: [],
        };
        setActivePlan(optimistic);
        setActiveThreadId(threadId);
        // Default: alla steg ikryssade — användaren tar bort det hon ej vill ha
        setApprovedIds(new Set(steps.map((s) => s.id)));
        // Reset execution view for the new plan
        setExecution(null);
        setExecutionError(null);
        setIsExecuting(false);
        setTab("active");
        setPrompt("");
        showToast(`Plan skapad med ${steps.length} steg.`, "success");
        // Refresh history in the background
        loadHistory();
      } catch (err) {
        showToast(
          err instanceof Error ? err.message : "Kunde inte skapa plan",
          "error"
        );
      }
    });
  };

  // ── Approve selected steps ────────────────────────────────────────────────
  const handleApprove = () => {
    if (!activePlan) return;
    const ids = Array.from(approvedIds);
    if (ids.length === 0) {
      showToast("Välj minst ett steg att godkänna.", "info");
      return;
    }
    startApproveTransition(async () => {
      try {
        const { executing } = await approvePlan(
          activePlan.id,
          ids,
          activeThreadId
        );
        setActivePlan({
          ...activePlan,
          status: executing ? "executing" : "approved",
          approved_at: new Date().toISOString(),
          approved_step_ids: ids,
          executing,
        });
        // Kick off polling immediately so the cockpit shows movement
        if (executing) {
          setIsExecuting(true);
          setExecution(null);
          setExecutionError(null);
        }
        showToast(
          executing
            ? `${ids.length} steg godkända — körning startad.`
            : `${ids.length} steg godkända.`,
          "success"
        );
        loadHistory();
      } catch (err) {
        showToast(
          err instanceof Error ? err.message : "Godkännande misslyckades",
          "error"
        );
      }
    });
  };

  // ── Live execution polling ────────────────────────────────────────────────
  const fetchExecution = useCallback(
    async (planId: string, threadId: string | null) => {
      try {
        const view = await getPlanExecution(planId, threadId);
        setExecution(view);
        setExecutionError(null);
        if (!view.isActive && view.jobs.length > 0) {
          // Terminal: nothing left pending/running.
          setIsExecuting(false);
        }
        return view;
      } catch (err) {
        setExecutionError(
          err instanceof Error ? err.message : "Kunde inte hämta körstatus"
        );
        return null;
      }
    },
    []
  );

  useEffect(() => {
    // Clear any prior interval before deciding whether to start a new one.
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (!activePlan || !isExecuting) return;

    const planId = activePlan.id;
    const threadId = activeThreadId;

    // Fire immediately, then every 3s while still active.
    void fetchExecution(planId, threadId);
    pollTimerRef.current = setInterval(() => {
      void fetchExecution(planId, threadId);
    }, 3000);

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [activePlan, activeThreadId, isExecuting, fetchExecution]);

  const toggleStep = (id: string) => {
    setApprovedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-5">
      {/* Breadcrumb */}
      <button
        onClick={() => router.push("/")}
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-blue-600 mb-4"
      >
        <ChevronLeft className="w-4 h-4" /> Tillbaka till databasen
      </button>

      {/* Header */}
      <div className="flex items-center gap-3 mb-2 flex-wrap">
        <SavantLogo size={32} />
        <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-violet-600" />
          Orkestrator
        </h1>
      </div>
      <p className="text-sm text-gray-500 mb-6">
        Beskriv ett mål — agenten föreslår en plan med tier-prioriterade steg
        (gratis öppna data först, betalda källor sist) och väntar på ditt
        godkännande innan körning.
      </p>

      {/* Tabs + sidonav till Agent Memory */}
      <div className="flex items-end justify-between gap-2 border-b border-gray-200 mb-5 flex-wrap">
        <div className="flex gap-1">
          {TABS.map((t) => {
            const Icon = t.icon;
            const isActive = tab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  isActive
                    ? "border-blue-600 text-blue-700"
                    : "border-transparent text-gray-500 hover:text-blue-700 hover:border-blue-200"
                }`}
              >
                <Icon className="w-4 h-4" />
                {t.label}
                {t.id === "active" && activePlan && (
                  <span className="ml-1 text-[10px] font-bold uppercase text-violet-600">
                    •
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <button
          onClick={() => router.push("/orchestrator/memory")}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 mb-1 rounded-lg text-sm font-medium text-violet-700 hover:bg-violet-50 transition-colors"
          title="Bläddra och sök i agentens långtidsminne"
        >
          <Brain className="w-4 h-4" />
          Agentminne
        </button>
      </div>

      {/* ── Tab: Nytt plan ────────────────────────────────────────────────── */}
      {tab === "new" && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
          <h2 className="text-sm font-bold text-gray-900 mb-2 flex items-center gap-2">
            <Lightbulb className="w-4 h-4 text-amber-500" />
            Be agenten om en ny plan
          </h2>
          <p className="text-sm text-gray-600 mb-4">
            Var konkret om bransch, region och rollerna du letar efter. Agenten
            använder vault-anteckningar (RAG) för att undvika dubbelarbete.
          </p>

          <DataCaptureButtons onPick={setPrompt} disabled={pendingPlan} />

          <PromptSuggestions onPick={setPrompt} disabled={pendingPlan} />

          <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1.5">
            Beskrivning
          </label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Beskriv vad du vill att agenten ska hitta..."
            rows={6}
            className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none resize-y"
            disabled={pendingPlan}
          />

          <div className="mt-4 flex items-center justify-between flex-wrap gap-2">
            <p className="text-xs text-gray-400">
              Exempel: &ldquo;Hitta CTO på IT-konsultbolag i Skåne, 50–250
              anställda&rdquo;
            </p>
            <Button
              variant="primary"
              size="md"
              onClick={handleGenerate}
              disabled={pendingPlan || !prompt.trim()}
            >
              {pendingPlan ? "Genererar..." : "Generera plan"}
              <Send className="w-4 h-4" />
            </Button>
          </div>
        </div>
      )}

      {/* ── Tab: Aktiv plan ───────────────────────────────────────────────── */}
      {tab === "active" && (
        <div>
          {activeLoading && (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 text-sm text-gray-500">
              Laddar plan...
            </div>
          )}

          {!activeLoading && !activePlan && (
            <div className="bg-white rounded-xl border border-dashed border-gray-300 p-8 text-center">
              <Lightbulb className="w-8 h-8 text-gray-300 mx-auto mb-3" />
              <p className="text-sm text-gray-500 mb-3">
                Ingen aktiv plan. Skapa en ny eller välj från historiken.
              </p>
              <div className="flex items-center justify-center gap-2">
                <Button variant="primary" size="sm" onClick={() => setTab("new")}>
                  Skapa ny plan
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setTab("history")}
                >
                  Visa historiken
                </Button>
              </div>
            </div>
          )}

          {!activeLoading && activePlan && (
            <ActivePlanView
              plan={activePlan}
              approvedIds={approvedIds}
              onToggle={toggleStep}
              onApprove={handleApprove}
              pending={pendingApprove}
              execution={execution}
              executionError={executionError}
              isExecuting={isExecuting}
            />
          )}
        </div>
      )}

      {/* ── Tab: Senaste planer ──────────────────────────────────────────── */}
      {tab === "history" && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h2 className="text-sm font-bold text-gray-900 flex items-center gap-2">
              <History className="w-4 h-4 text-gray-500" />
              Senaste 10 planer
            </h2>
            <button
              onClick={loadHistory}
              disabled={historyLoading}
              className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-blue-600"
            >
              <RefreshCcw
                className={`w-3.5 h-3.5 ${historyLoading ? "animate-spin" : ""}`}
              />
              Uppdatera
            </button>
          </div>

          {historyError && (
            <div className="flex items-start gap-2 p-3 mb-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span className="break-words">{historyError}</span>
            </div>
          )}

          {!historyError && historyLoading && history.length === 0 && (
            <p className="text-sm text-gray-400">Hämtar...</p>
          )}

          {!historyError && !historyLoading && history.length === 0 && (
            <p className="text-sm text-gray-400">Inga planer ännu.</p>
          )}

          {history.length > 0 && (
            <ul className="divide-y divide-gray-100">
              {history.map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => loadPlanIntoActive(p.id)}
                    className="w-full text-left py-3 px-1 hover:bg-blue-50/50 rounded-lg transition-colors group"
                  >
                    <div className="flex items-start justify-between gap-3 flex-wrap">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 group-hover:text-blue-700 line-clamp-2">
                          {p.user_prompt}
                        </p>
                        <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
                          <span>{formatDate(p.created_at)}</span>
                          <span className="inline-flex items-center gap-1">
                            <Layers className="w-3 h-3" />
                            {p.step_count} steg
                          </span>
                          {p.approved_count > 0 && (
                            <span className="inline-flex items-center gap-1 text-emerald-700">
                              <CheckCircle2 className="w-3 h-3" />
                              {p.approved_count} godkända
                            </span>
                          )}
                        </div>
                      </div>
                      <StatusBadge status={p.status} />
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// ActivePlanView — renderizza il piano corrente con checkboxes per step
// ─────────────────────────────────────────────────────────────────────────────

function ActivePlanView({
  plan,
  approvedIds,
  onToggle,
  onApprove,
  pending,
  execution,
  executionError,
  isExecuting,
}: {
  plan: PlanFull;
  approvedIds: Set<string>;
  onToggle: (id: string) => void;
  onApprove: () => void;
  pending: boolean;
  execution: PlanExecutionView | null;
  executionError: string | null;
  isExecuting: boolean;
}) {
  const selectedCount = approvedIds.size;
  const totalCount = plan.steps.length;
  const isReadonly = plan.status !== "draft";

  return (
    <div className="space-y-4">
      {/* Header card */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <div className="flex items-start justify-between gap-3 flex-wrap mb-2">
          <div className="flex-1 min-w-0">
            <p className="text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-1">
              Prompt
            </p>
            <p className="text-sm text-gray-900 font-medium break-words">
              {plan.user_prompt}
            </p>
          </div>
          <StatusBadge status={plan.status} />
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-500 mt-3 pt-3 border-t border-gray-100 flex-wrap">
          <span className="inline-flex items-center gap-1">
            <FileText className="w-3.5 h-3.5" />
            Plan-ID: <code className="text-[11px] bg-gray-100 px-1 rounded">{plan.id.slice(0, 8)}</code>
          </span>
          <span>Skapad {formatDate(plan.created_at)}</span>
          {plan.approved_at && (
            <span className="text-emerald-700">
              Godkänd {formatDate(plan.approved_at)}
            </span>
          )}
        </div>
      </div>

      {/* Steps list */}
      <div className="space-y-3">
        {plan.steps.length === 0 ? (
          <div className="bg-white rounded-xl border border-dashed border-gray-300 p-6 text-center text-sm text-gray-400">
            Planen innehåller inga steg.
          </div>
        ) : (
          plan.steps.map((step, idx) => (
            <StepCard
              key={step.id}
              step={step}
              index={idx}
              checked={approvedIds.has(step.id)}
              onToggle={() => onToggle(step.id)}
              disabled={isReadonly}
            />
          ))
        )}
      </div>

      {/* Approve bar */}
      {!isReadonly && plan.steps.length > 0 && (
        <div className="sticky bottom-4 bg-white rounded-xl border border-gray-200 shadow-md p-4 flex items-center justify-between flex-wrap gap-3">
          <div className="text-sm text-gray-700">
            <span className="font-semibold text-gray-900">{selectedCount}</span>
            <span className="text-gray-500"> / {totalCount} steg valda</span>
          </div>
          <Button
            variant="accent"
            size="md"
            onClick={onApprove}
            disabled={pending || selectedCount === 0}
          >
            {pending ? "Godkänner..." : "Godkänn markerade steg"}
            <CheckCircle2 className="w-4 h-4" />
          </Button>
        </div>
      )}

      {isReadonly && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 text-sm text-emerald-800 flex items-start gap-2">
          <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">
              Planen är {STATUS_LABELS[plan.status]?.toLowerCase() ?? plan.status}.
            </p>
            <p className="text-xs text-emerald-700 mt-0.5">
              {plan.approved_step_ids.length} steg är godkända och redo att köras
              av EXECUTE-fasen.
            </p>
          </div>
        </div>
      )}

      {/* ── Live execution view (Fase 12) ───────────────────────────────── */}
      {(isExecuting || (execution && execution.jobs.length > 0)) && (
        <ExecutionView
          execution={execution}
          executionError={executionError}
          isExecuting={isExecuting}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// ExecutionView — live scrape_jobs status while EXECUTE chain runs
// ─────────────────────────────────────────────────────────────────────────────

const JOB_STATUS_STYLES: Record<string, { badge: string; icon: typeof Loader2 }> = {
  pending: {
    badge: "bg-gray-100 text-gray-700 border-gray-300",
    icon: Clock,
  },
  running: {
    badge: "bg-blue-50 text-blue-700 border-blue-200",
    icon: Loader2,
  },
  done: {
    badge: "bg-emerald-50 text-emerald-700 border-emerald-200",
    icon: CheckCircle2,
  },
  blocked: {
    badge: "bg-amber-50 text-amber-700 border-amber-200",
    icon: ShieldAlert,
  },
  failed: {
    badge: "bg-red-50 text-red-700 border-red-200",
    icon: XCircle,
  },
};

const JOB_STATUS_LABELS: Record<string, string> = {
  pending: "Väntar",
  running: "Kör",
  done: "Klar",
  blocked: "Blockerad",
  failed: "Misslyckad",
};

function ExecutionView({
  execution,
  executionError,
  isExecuting,
}: {
  execution: PlanExecutionView | null;
  executionError: string | null;
  isExecuting: boolean;
}) {
  const jobs = execution?.jobs ?? [];
  const counts = execution?.counts ?? {};
  const totalTimeMs = computeTotalTime(jobs);

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
        <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
          <PlayCircle className="w-4 h-4 text-blue-600" />
          Körning
          {isExecuting && (
            <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin" />
          )}
        </h3>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          {totalTimeMs > 0 && (
            <span className="inline-flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {formatDuration(totalTimeMs)}
            </span>
          )}
          <span>{jobs.length} jobb</span>
        </div>
      </div>

      {/* Count badges */}
      <div className="flex items-center gap-1.5 flex-wrap mb-3">
        {(["pending", "running", "done", "blocked", "failed"] as const).map((s) => {
          const c = counts[s] ?? 0;
          if (c === 0) return null;
          const style = JOB_STATUS_STYLES[s];
          const Icon = style.icon;
          return (
            <span
              key={s}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold border ${style.badge}`}
            >
              <Icon
                className={`w-3 h-3 ${s === "running" ? "animate-spin" : ""}`}
              />
              {JOB_STATUS_LABELS[s]} · {c}
            </span>
          );
        })}
      </div>

      {executionError && (
        <div className="flex items-start gap-2 p-3 mb-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span className="break-words">{executionError}</span>
        </div>
      )}

      {jobs.length === 0 && !executionError && (
        <p className="text-sm text-gray-400">
          {isExecuting
            ? "Väntar på att första jobbet ska startas..."
            : "Inga körningar registrerade för denna plan ännu."}
        </p>
      )}

      {jobs.length > 0 && (
        <ul className="divide-y divide-gray-100">
          {jobs.map((job) => (
            <JobRow key={job.id} job={job} />
          ))}
        </ul>
      )}
    </div>
  );
}

function JobRow({ job }: { job: ScrapeJobRow }) {
  const style = JOB_STATUS_STYLES[job.status] ?? JOB_STATUS_STYLES.pending;
  const Icon = style.icon;
  return (
    <li className="py-2.5 flex items-start gap-3">
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold border shrink-0 ${style.badge}`}
      >
        <Icon
          className={`w-3 h-3 ${job.status === "running" ? "animate-spin" : ""}`}
        />
        {JOB_STATUS_LABELS[job.status] ?? job.status}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-900 truncate" title={job.query ?? ""}>
          {job.query || "(ingen query)"}
        </p>
        <div className="flex items-center gap-3 text-[11px] text-gray-500 mt-0.5 flex-wrap">
          {typeof job.tier_used === "number" && (
            <span className="inline-flex items-center gap-1">
              <Layers className="w-3 h-3" />T{job.tier_used}
            </span>
          )}
          {job.target_domain && (
            <span className="inline-flex items-center gap-1">
              <Target className="w-3 h-3" />
              {job.target_domain}
            </span>
          )}
          {typeof job.result_count === "number" && (
            <span>{job.result_count} resultat</span>
          )}
          {job.error_message && (
            <span className="text-red-600">err: {job.error_message}</span>
          )}
          {job.blocked_reason && (
            <span className="text-amber-700">
              blocked: {job.blocked_reason}
            </span>
          )}
        </div>
      </div>
    </li>
  );
}

function computeTotalTime(jobs: ScrapeJobRow[]): number {
  if (jobs.length === 0) return 0;
  const starts = jobs
    .map((j) => (j.started_at ? Date.parse(j.started_at) : NaN))
    .filter((n) => Number.isFinite(n));
  if (starts.length === 0) return 0;
  const min = Math.min(...starts);
  const ends = jobs
    .map((j) => (j.finished_at ? Date.parse(j.finished_at) : Date.now()))
    .filter((n) => Number.isFinite(n));
  const max = ends.length > 0 ? Math.max(...ends) : Date.now();
  return Math.max(0, max - min);
}

function formatDuration(ms: number): string {
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

// ─────────────────────────────────────────────────────────────────────────────
// StepCard — un singolo step proposto
// ─────────────────────────────────────────────────────────────────────────────

function StepCard({
  step,
  index,
  checked,
  onToggle,
  disabled,
}: {
  step: PlanStep;
  index: number;
  checked: boolean;
  onToggle: () => void;
  disabled: boolean;
}) {
  const t = tierStyle(step.tier);
  return (
    <div
      className={`bg-white rounded-xl border shadow-sm p-4 transition-colors ${
        checked && !disabled
          ? "border-blue-300 ring-1 ring-blue-100"
          : "border-gray-200"
      }`}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          disabled={disabled}
          className="mt-1 w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 cursor-pointer disabled:cursor-not-allowed"
          aria-label={`Godkänn steg ${index + 1}`}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1.5">
            <span className="text-[11px] font-bold uppercase tracking-wider text-gray-400">
              Steg {index + 1}
            </span>
            <span
              className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold border ${t.badge}`}
              title={t.label}
            >
              {t.label}
            </span>
            <span className="inline-flex items-center gap-1 text-[11px] font-medium text-gray-500">
              <Target className="w-3 h-3" />
              {step.source}
            </span>
          </div>
          <p className="text-sm font-medium text-gray-900 break-words mb-2">
            {step.query}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
            <div>
              <p className="font-semibold uppercase tracking-wider text-gray-400 text-[10px] mb-0.5">
                Förväntad output
              </p>
              <p className="text-gray-700 break-words">{step.expected_yield}</p>
            </div>
            <div>
              <p className="font-semibold uppercase tracking-wider text-gray-400 text-[10px] mb-0.5">
                Motivering
              </p>
              <p className="text-gray-700 break-words">{step.rationale}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
