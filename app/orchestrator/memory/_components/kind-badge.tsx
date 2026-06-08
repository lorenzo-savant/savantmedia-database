import { BookOpen, Code2, History, Lightbulb } from "lucide-react";
import type { KnowledgeKind } from "@/lib/actions/memory";

const KIND_STYLES: Record<
  KnowledgeKind,
  { badge: string; label: string; icon: typeof Lightbulb }
> = {
  playbook: {
    badge: "bg-indigo-50 text-indigo-700 border-indigo-200",
    label: "Playbook",
    icon: BookOpen,
  },
  snippet: {
    badge: "bg-blue-50 text-blue-700 border-blue-200",
    label: "Snippet",
    icon: Code2,
  },
  query_log: {
    badge: "bg-gray-100 text-gray-700 border-gray-300",
    label: "Query-logg",
    icon: History,
  },
  lesson: {
    badge: "bg-emerald-50 text-emerald-700 border-emerald-200",
    label: "Lärdom",
    icon: Lightbulb,
  },
};

export function kindStyle(kind: KnowledgeKind) {
  return KIND_STYLES[kind] ?? KIND_STYLES.snippet;
}

export function KindBadge({ kind }: { kind: KnowledgeKind }) {
  const s = kindStyle(kind);
  const Icon = s.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold border ${s.badge}`}
      title={s.label}
    >
      <Icon className="w-3 h-3" />
      {s.label}
    </span>
  );
}
