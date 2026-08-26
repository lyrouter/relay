/**
 * Names for the shapes the UI actually uses — **aliases over generated types**.
 *
 * `schema.d.ts` is generated from the application's own OpenAPI document
 * (`npm run types`), which is the point of API-5's fourth clause: if a `/web`
 * response loses a field, this file stops compiling and the mismatch surfaces at
 * build time instead of as `undefined` in a template.
 *
 * The `relay__api__web__…` names are FastAPI's disambiguation — `/web` and
 * `/api/v1` each define a `TicketResponse`, so the generator qualifies both by
 * module. Aliasing them here means exactly one place knows those long names, and
 * a component never accidentally imports the **public API's** shape while talking
 * to the web surface. The two are allowed to differ: that is the whole point of
 * `/web` being versionless (§8.9).
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Ticket = Schemas["relay__api__web__tickets__TicketResponse"];
export type TicketPage = Schemas["relay__api__web__tickets__TicketPage"];
export type CreateTicket = Schemas["relay__api__web__tickets__CreateTicketPayload"];
export type UpdateTicket = Schemas["relay__api__web__tickets__UpdateTicketPayload"];
export type TicketComment = Schemas["CommentResponse"];
export type TicketHistoryEntry = Schemas["HistoryResponse"];
export type TicketStatus = Schemas["TicketStatus"];
export type TicketType = Schemas["TicketType"];
export type Priority = Schemas["Priority"];

export type Log = Schemas["LogResponse"];
export type CreateLog = Schemas["CreateLogPayload"];
export type SaveLog = Schemas["SaveLogPayload"];
export type LogVersion = Schemas["VersionResponse"];
export type DiffLine = Schemas["DiffLineResponse"];
export type ShareLevel = Schemas["ShareLevel"];
export type LogFormat = Schemas["LogFormat"];
export type EditLock = Schemas["LockResponse"];

export type Session = Schemas["SessionResponse"];
export type Member = Schemas["MemberResponse"];
export type AdminUser = Schemas["AdminUserResponse"];
export type SignupResult = Schemas["SignupResponse"];
export type VerifyResult = Schemas["VerifyResponse"];
export type Role = Schemas["Role"];
export type UserStatus = Schemas["UserStatus"];
export type Label = Schemas["LabelResponse"];
export type Iteration = Schemas["IterationResponse"];
export type TicketField = Schemas["relay__api__web__meta__TicketFieldResponse"];
export type InboxItem = Schemas["InboxItemResponse"];
export type Attachment = Schemas["AttachmentResponse"];
export type SearchResult = Schemas["SearchResponse"];
export type SearchHit = Schemas["HitResponse"];

/**
 * The six statuses, in board order — **frozen on release** (§7.2 / clarification 2.2).
 *
 * Ordered here rather than sorted at each call site because "board order" is a
 * product decision (the left-to-right flow of work), not alphabetical.
 */
export const STATUS_ORDER: TicketStatus[] = [
  "new",
  "assign",
  "working",
  "resolved",
  "reopen",
  "closed",
];

/**
 * Display names live **only** in the frontend (§8.3).
 *
 * The wire values are lowercase snake_case so they never appear in a URL, a log
 * key or a consumer's constant name with a space or an apostrophe in them.
 */
export const STATUS_LABELS: Record<TicketStatus, string> = {
  new: "新建",
  assign: "已指派",
  working: "处理中",
  resolved: "已解决",
  reopen: "重开",
  closed: "已关闭",
};

export const PRIORITY_LABELS: Record<Priority, string> = {
  p0: "P0 立即",
  p1: "P1 高",
  p2: "P2 中",
  p3: "P3 低",
};

export const TYPE_LABELS: Record<TicketType, string> = {
  bug: "缺陷",
  feature: "需求",
  task: "任务",
};

export type SupportCategory = Schemas["SupportCategory"];

export const CATEGORY_LABELS: Record<SupportCategory, string> = {
  presale: "售前咨询",
  aftersale: "售后问题",
  billing: "账单与充值",
  technical: "技术支持",
  feedback: "意见反馈",
  other: "其他",
};

/** Clarification 2.2: no status currently requires a written reason. */
export const STATUSES_REQUIRING_REASON: TicketStatus[] = [];

export const SHARE_LABELS: Record<ShareLevel, string> = {
  private: "L0 仅自己与管理员",
  named: "L1 指定同事",
  space: "L2 所属空间",
  tenant: "L3 全公司",
};

export const ROLE_LABELS: Record<Role, string> = {
  admin: "管理员",
  member: "成员",
  guest: "访客",
};

export const USER_STATUS_LABELS: Record<UserStatus, string> = {
  pending: "待审批",
  active: "正常",
  deactivated: "已停用",
};

export const ROLES: Role[] = ["admin", "member", "guest"];
