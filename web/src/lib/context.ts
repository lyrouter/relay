/**
 * AI-context helpers shared by cards, 此刻, and the chain detail.
 *
 * Empty values stay visible: MVP fields are often blank, and hiding them makes
 * the product look like a plain tracker again.
 */
import type { Ticket, TicketComment, TicketHistoryEntry } from "@/api/types";

export type ChainKind = "alert" | "im" | "ticket" | "trace" | "log" | "pr";

export interface ChainNode {
  id: ChainKind;
  label: string;
  detail?: string;
  at?: string | null;
  present: boolean;
}

export type TimelineItem =
  | { kind: "note"; at: string; comment: TicketComment }
  | { kind: "transition"; at: string; entry: TicketHistoryEntry };

export function formatContextValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.map(String).filter(Boolean).join("、");
  return String(value);
}

export function contextSlice(
  ticket: Ticket,
  keys: string[] = ["trace_id", "model", "provider", "error_class"],
): { key: string; value: string }[] {
  const ctx = ticket.ai_context ?? {};
  return keys
    .map((key) => ({ key, value: formatContextValue(ctx[key]) }))
    .filter((one) => one.value);
}

/** Honest chain: light up nodes we can infer from S1 fields; leave the rest empty. */
export function buildChain(ticket: Ticket): ChainNode[] {
  const ctx = ticket.ai_context ?? {};
  const source = (ticket.source ?? "").toLowerCase();
  const fromIm =
    Boolean(ticket.submitter) ||
    /wecom|wechat|企微|bot|im/.test(source);
  const fromAlert = Boolean(ctx.error_class) || /alert|pager|monitor|告警/.test(source);
  const traces = formatContextValue(ctx.trace_id);
  const errorClass = formatContextValue(ctx.error_class);

  return [
    {
      id: "alert",
      label: "告警",
      detail: errorClass || undefined,
      present: fromAlert,
      at: null,
    },
    {
      id: "im",
      label: "企微",
      detail: fromIm ? ticket.source ?? "群内建单" : undefined,
      present: fromIm,
      at: fromIm ? ticket.created_at : null,
    },
    {
      id: "ticket",
      label: "工单",
      detail: ticket.key,
      present: true,
      at: ticket.created_at,
    },
    {
      id: "trace",
      label: "遥测",
      detail: traces || undefined,
      present: Boolean(traces),
      at: null,
    },
    {
      id: "log",
      label: "日志",
      detail: undefined,
      present: false,
      at: null,
    },
    {
      id: "pr",
      label: "PR",
      detail: ticket.pr_url ?? undefined,
      present: Boolean(ticket.pr_url),
      at: null,
    },
  ];
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
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}
