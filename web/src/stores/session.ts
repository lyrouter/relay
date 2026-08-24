/**
 * The signed-in user, their capabilities, and the tenant they belong to.
 *
 * **Capabilities come from the server, and the UI must not re-derive them.**
 * `/web/session` returns the capability list the backend computed from the user's
 * *current* role, and every "may I show this button?" decision reads it. The
 * alternative — a `role === "admin"` check in a template — is a second copy of the
 * permission matrix that drifts from the real one, and the drift is invisible
 * until somebody clicks a button that 403s.
 *
 * **The tenant slug is loaded here because every permalink needs it** (S-12 /
 * TKT-9). With one tenant the UI may hide the segment, but the router carries it
 * from day one — shipping `/t/331` first would make the second tenant a breaking
 * change in every link anybody had saved.
 */
import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { api, ProblemError } from "@/api/client";
import type { Session } from "@/api/types";

export const useSessionStore = defineStore("session", () => {
  const session = ref<Session | null>(null);
  const loading = ref(false);
  /** True once we have *asked* — distinct from "is signed in". */
  const resolved = ref(false);
  const mfaRequired = ref(false);
  const error = ref<string | null>(null);

  const signedIn = computed(() => session.value !== null);
  const tenantSlug = computed(() => session.value?.tenant.slug ?? "");
  const capabilities = computed(() => new Set(session.value?.capabilities ?? []));

  /** The one permission question a component should ask. See the module note. */
  function can(capability: string): boolean {
    return capabilities.value.has(capability);
  }

  async function load(): Promise<void> {
    loading.value = true;
    try {
      session.value = await api.get<Session>("/web/session");
      mfaRequired.value = false;
    } catch (caught) {
      // 401 is the ordinary "not signed in" answer, not an error to show. Any
      // other failure is worth surfacing: a 500 here means the app is broken and
      // a blank login screen would hide that.
      if (caught instanceof ProblemError && caught.isUnauthenticated) {
        session.value = null;
        mfaRequired.value = caught.needsMfa;
      } else {
        error.value = caught instanceof Error ? caught.message : String(caught);
      }
    } finally {
      loading.value = false;
      resolved.value = true;
    }
  }

  async function login(email: string, password: string): Promise<boolean> {
    error.value = null;
    try {
      const result = await api.post<{ mfa_required: boolean }>("/web/auth/login", {
        email,
        password,
      });
      mfaRequired.value = result.mfa_required;
      // A second factor is the next *step*, not a failure: the session cookie is
      // already set but half-open, and only the TOTP route accepts it.
      if (result.mfa_required) return false;
      await load();
      return signedIn.value;
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught);
      return false;
    }
  }

  async function submitTotp(code: string): Promise<boolean> {
    error.value = null;
    try {
      await api.post("/web/auth/totp", { code });
      await load();
      mfaRequired.value = false;
      return signedIn.value;
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught);
      return false;
    }
  }

  async function logout(): Promise<void> {
    try {
      await api.post("/web/auth/logout");
    } finally {
      // Cleared even if the request failed: the user asked to leave, and leaving
      // them looking at a populated UI would be worse than a stale server-side
      // session that expires on its own.
      session.value = null;
      mfaRequired.value = false;
    }
  }

  return {
    session,
    loading,
    resolved,
    mfaRequired,
    error,
    signedIn,
    tenantSlug,
    capabilities,
    can,
    load,
    login,
    submitTotp,
    logout,
  };
});
