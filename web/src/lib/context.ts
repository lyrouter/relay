/**
 * Shared display helpers for the mockup-aligned UI.
 */
import type { Ticket, TicketComment, TicketHistoryEntry } from "@/api/types";

export type ChainKind = "alert" | "im" | "ticket" | "trace" | "log" | "pr";

export interface ChainNode {
  id: ChainKind;
  label: string;
  title: string;
  detail?: string;
  at?: string | null;
  present: boolean;
  active?: boolean;
}

export type TimelineItem =
  | { kind: "note"; at: string; comment: TicketComment }
  | { kind: "transition"; at: string; entry: TicketHistoryEntry };

export function formatContextValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.map(String).filter(Boolean).join("、");
  return String(value);
}

export function contextValue(ticket: Ticket | null | undefined, key: string): string {
  if (!ticket) return "";
  return formatContextValue(ticket.ai_context?.[key]);
}

export function firstModel(ticket: Ticket): string {
  return contextValue(ticket, "model").split("、")[0] || "";
}

/** Soft badge tone by model family — matches now.png color coding. */
export function modelTone(model: string): "green" | "blue" | "violet" | "gray" {
  const m = model.toLowerCase();
  if (!m) return "gray";
  if (m.includes("claude")) return "violet";
  if (m.includes("mini") || m.includes("haiku")) return "blue";
  if (m.includes("gpt") || m.includes("o1") || m.includes("o3")) return "green";
  return "blue";
}

export function initials(name: string | null | undefined): string {
  if (!name || name === "—" || name === "（已停用）") return "?";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2);
}

export function buildChain(ticket: Ticket, activeId?: ChainKind): ChainNode[] {
  const ctx = ticket.ai_context ?? {};
  const source = (ticket.source ?? "").toLowerCase();
  const fromIm =
    Boolean(ticket.submitter) || /wecom|wechat|企微|bot|im|feishu|lark/.test(source);
  const fromAlert = Boolean(ctx.error_class) || /alert|pager|monitor|告警/.test(source);
  const traces = formatContextValue(ctx.trace_id);
  const errorClass = formatContextValue(ctx.error_class);
  const preferred: ChainKind =
    activeId ??
    (traces ? "trace" : fromAlert ? "alert" : fromIm ? "im" : "ticket");

  const nodes: ChainNode[] = [
    {
      id: "alert",
      label: "告警",
      title: errorClass ? `告警 · ${errorClass}` : "告警",
      detail: fromAlert ? errorClass || "监控触发" : undefined,
      present: fromAlert,
      at: null,
    },
    {
      id: "im",
      label: "群聊",
      title: "群聊 · @Relay 建单",
      detail: fromIm ? ticket.source ?? "企微建单" : undefined,
      present: fromIm,
      at: fromIm ? ticket.created_at : null,
    },
    {
      id: "ticket",
      label: "工单",
      title: "工单创建",
      detail: ticket.key,
      present: true,
      at: ticket.created_at,
    },
    {
      id: "trace",
      label: "遥测",
      title: traces ? `遥测 · trace ${traces.slice(0, 8)}…` : "遥测",
      detail: traces || undefined,
      present: Boolean(traces),
      at: null,
    },
    {
      id: "log",
      label: "日志",
      title: "复盘日志",
      detail: undefined,
      present: false,
      at: null,
    },
    {
      id: "pr",
      label: "PR",
      title: "关联 PR",
      detail: ticket.pr_url ?? undefined,
      present: Boolean(ticket.pr_url),
      at: null,
    },
  ];

  return nodes.map((node) => ({
    ...node,
    active: node.id === preferred && node.present,
  }));
}

export function buildTimeline(
  comments: TicketComment[],
  history: TicketHistoryEntry[],
): TimelineItem[] {
  const items: TimelineItem[] = [
    ...comments
      .filter((one) => one.created_at)
      .map((comment) => ({ kind: "note" as const, at: comment.created_at as string, comment })),
    ...history
      .filter((one) => one.created_at)
      .map((entry) => ({ kind: "transition" as const, at: entry.created_at as string, entry })),
  ];
  return items.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms) || ms < 0) return new Date(iso).toLocaleString();
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes}m 前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h 前`;
  const days = Math.floor(hours / 24);
  return `${days}d 前`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    /* ignore */
  }
}
