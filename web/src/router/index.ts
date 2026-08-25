/**
 * Routes, and the one that is a **contract**.
 *
 * `/:tenantSlug/t/:number` is TKT-9 / S-12: a permalink carries a tenant segment
 * from day one. With a single tenant the UI may hide it — the redirect below does
 * exactly that for the bare `/t/331` — but the router has to *support* it now,
 * because shipping `/t/331` first would make the second tenant a breaking change
 * in every link anybody had already saved or pasted into a Jira ticket.
 *
 * App chrome (此刻 / 上下文 / 工作 / 知识) follows the relay-ui skill and can be
 * renamed freely. Only the permalink shape is frozen (§8.6's last bullet).
 *
 * The guard resolves the session **once** and then trusts the store. A guard that
 * re-fetched on every navigation would put a request in front of every click; a
 * 401 from any subsequent call sends the user back here anyway.
 */
import { createRouter, createWebHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";

import { useSessionStore } from "@/stores/session";

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: { name: "now" } },
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/LoginView.vue"),
    meta: { public: true },
  },
  {
    path: "/signup",
    name: "signup",
    component: () => import("@/views/SignupView.vue"),
    meta: { public: true },
  },
  {
    // Mail from AC-1 lands here. The token is consumed by POST /web/auth/verify.
    path: "/verify",
    name: "verify",
    component: () => import("@/views/VerifyView.vue"),
    meta: { public: true },
  },
  {
    // Mail from an Admin invitation lands here (`/invite?token=…`).
    path: "/invite",
    name: "invite",
    component: () => import("@/views/InviteView.vue"),
    meta: { public: true },
  },
  {
    path: "/now",
    name: "now",
    component: () => import("@/views/NowView.vue"),
  },
  {
    path: "/context",
    name: "context",
    component: () => import("@/views/TicketsView.vue"),
    meta: { surface: "context" },
  },
  {
    path: "/work",
    component: () => import("@/views/WorkView.vue"),
    children: [
      {
        path: "",
        name: "work-list",
        component: () => import("@/views/TicketsView.vue"),
        meta: { surface: "work" },
      },
      {
        path: "board",
        name: "board",
        component: () => import("@/views/BoardView.vue"),
      },
      {
        path: "mine",
        name: "my-tickets",
        component: () => import("@/views/MyTicketsView.vue"),
      },
    ],
  },
  {
    path: "/logs",
    name: "logs",
    component: () => import("@/views/LogsView.vue"),
  },
  {
    path: "/logs/new",
    name: "log-new",
    component: () => import("@/views/LogEditorView.vue"),
  },
  {
    path: "/logs/:id",
    name: "log",
    component: () => import("@/views/LogEditorView.vue"),
  },
  {
    path: "/users",
    name: "users",
    component: () => import("@/views/UsersView.vue"),
  },
  // Legacy entity URLs → workflow IA (bookmarks / old docs).
  { path: "/tickets", redirect: { name: "context" } },
  { path: "/board", redirect: { name: "board" } },
  { path: "/my", redirect: { name: "my-tickets" } },
  {
    // **Frozen on release** (TKT-9 / S-12). The tenant segment is not optional.
    path: "/:tenantSlug/t/:number",
    name: "ticket",
    component: () => import("@/views/TicketDetailView.vue"),
  },
  {
    // The tenant-less form, kept as a *redirect* rather than a second route.
    // Convenient to type, and it resolves into the canonical URL so nobody ends
    // up sharing a link without the segment.
    path: "/t/:number",
    name: "ticket-short",
    redirect: (to) => ({
      name: "ticket",
      params: { tenantSlug: "-", number: to.params.number },
    }),
  },
  {
    path: "/:pathMatch(.*)*",
    name: "not-found",
    component: () => import("@/views/NotFoundView.vue"),
    meta: { public: true },
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
});

router.beforeEach(async (to) => {
  const session = useSessionStore();
  if (!session.resolved) await session.load();
  if (to.meta.public) return true;
  if (!session.signedIn) {
    return { name: "login", query: { next: to.fullPath } };
  }
  return true;
});
