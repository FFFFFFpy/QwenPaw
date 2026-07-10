import { request } from "../request";
import type {
  CodexAccountStatus,
  CodexLoginFlow,
  CodexLoginStart,
  CodexLoginStatus,
  CodexModelsRefresh,
  CodexRateLimits,
  CodexRuntimeStatus,
  CodexSubscriptionSettings,
  CodexSubscriptionSettingsUpdate,
} from "../types/codexSubscription";

const base = "/providers/openai-codex";

export const codexSubscriptionApi = {
  getRuntime: () => request<CodexRuntimeStatus>(`${base}/runtime`),
  redetectRuntime: () =>
    request<CodexRuntimeStatus>(`${base}/runtime/redetect`, {
      method: "POST",
    }),
  getAccount: () => request<CodexAccountStatus>(`${base}/account`),
  startLogin: (flow: CodexLoginFlow) =>
    request<CodexLoginStart>(`${base}/oauth/start`, {
      method: "POST",
      body: JSON.stringify({ flow }),
    }),
  getLoginStatus: (state: string) =>
    request<CodexLoginStatus>(
      `${base}/oauth/status?state=${encodeURIComponent(state)}`,
    ),
  cancelLogin: (state: string) =>
    request<void>(`${base}/oauth/cancel`, {
      method: "POST",
      body: JSON.stringify({ state }),
    }),
  logout: () => request<void>(`${base}/logout`, { method: "POST" }),
  getRateLimits: () => request<CodexRateLimits>(`${base}/rate-limits`),
  refreshModels: () =>
    request<CodexModelsRefresh>(`${base}/models/refresh`, {
      method: "POST",
    }),
  getSettings: () => request<CodexSubscriptionSettings>(`${base}/settings`),
  updateSettings: (body: CodexSubscriptionSettingsUpdate) =>
    request<CodexSubscriptionSettings>(`${base}/settings`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};
