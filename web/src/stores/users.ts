/**
 * The Admin account-review screen (R-2).
 *
 * Distinct from `meta.members`, which is the assignee picker: no addresses, no
 * leavers. This store talks to `/web/admin/users` and is gated by `user_manage`
 * on the server — a Member who loaded the route still gets a 403, not a list.
 */
import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { api } from "@/api/client";
import type { AdminUser, Role, UserStatus } from "@/api/types";

export const useUserStore = defineStore("users", () => {
  const items = ref<AdminUser[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const notice = ref<string | null>(null);
  const filter = ref<UserStatus | "all">("all");

  const visible = computed(() => {
    if (filter.value === "all") return items.value;
    return items.value.filter((one) => one.status === filter.value);
  });

  const pendingCount = computed(
    () => items.value.filter((one) => one.status === "pending").length,
  );

  async function load(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      items.value = await api.get<AdminUser[]>("/web/admin/users");
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught);
    } finally {
      loading.value = false;
    }
  }

  async function invite(email: string, role: Role): Promise<boolean> {
    error.value = null;
    notice.value = null;
    try {
      const result = await api.post<{ message: string }>("/web/admin/invitations", {
        email,
        role,
      });
      notice.value = result.message;
      return true;
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught);
      return false;
    }
  }

  async function approve(userId: string): Promise<boolean> {
    return mutate(() => api.post(`/web/admin/users/${userId}/approval`));
  }

  async function changeRole(userId: string, role: Role): Promise<boolean> {
    return mutate(() => api.put(`/web/admin/users/${userId}/role`, { role }));
  }

  async function deactivate(userId: string): Promise<boolean> {
    error.value = null;
    notice.value = null;
    try {
      const result = await api.post<{ sessions_ended: number }>(
        `/web/admin/users/${userId}/deactivation`,
      );
      notice.value = `已停用，同时结束了 ${result.sessions_ended} 个会话。`;
      await load();
      return true;
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught);
      return false;
    }
  }

  async function reactivate(userId: string): Promise<boolean> {
    return mutate(() => api.delete(`/web/admin/users/${userId}/deactivation`));
  }

  async function mutate(call: () => Promise<unknown>): Promise<boolean> {
    error.value = null;
    notice.value = null;
    try {
      await call();
      await load();
      return true;
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught);
      return false;
    }
  }

  return {
    items,
    visible,
    pendingCount,
    loading,
    error,
    notice,
    filter,
    load,
    invite,
    approve,
    changeRole,
    deactivate,
    reactivate,
  };
});
