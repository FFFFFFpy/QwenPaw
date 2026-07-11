import { request } from "../request";
import type {
  CodexAccountStatus,
  CodexChatModelSettings,
  CodexImageModelSettings,
  CodexLoginStart,
  CodexLoginStatus,
  CodexModelsRefresh,
  CodexRateLimits,
  CodexSubscriptionSettings,
  CodexSubscriptionSettingsUpdate,
} from "../types/codexSubscription";

const base = "/providers/openai-codex";

export const codexSubscriptionApi = {
  getAccount: () => request<CodexAccountStatus>(`${base}/account`),
  startLogin: () =>
    request<CodexLoginStart>(`${base}/oauth/start`, { method: "POST" }),
  completeLogin: (body: {
    callback_url?: string;
    code?: string;
    state?: string;
  }) =>
    request<CodexAccountStatus>(`${base}/oauth/complete`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getLoginStatus: (state: string) =>
    request<CodexLoginStatus>(
      `${base}/oauth/status?state=${encodeURIComponent(state)}`,
    ),
  logout: () => request<void>(`${base}/logout`, { method: "POST" }),
  getRateLimits: () => request<CodexRateLimits>(`${base}/rate-limits`),
  getModels: () => request<CodexModelsRefresh>(`${base}/models`),
  refreshModels: () =>
    request<CodexModelsRefresh>(`${base}/models/refresh`, { method: "POST" }),
  validate: () =>
    request<{ valid: boolean; message: string }>(`${base}/validate`, {
      method: "POST",
    }),
  getChatModelSettings: (modelId: string) =>
    request<CodexChatModelSettings>(
      `${base}/models/${encodeURIComponent(modelId)}/settings`,
    ),
  updateChatModelSettings: (modelId: string, body: CodexChatModelSettings) =>
    request<CodexChatModelSettings>(
      `${base}/models/${encodeURIComponent(modelId)}/settings`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  getImageModelSettings: (modelId: string) =>
    request<CodexImageModelSettings>(
      `${base}/image-models/${encodeURIComponent(modelId)}/settings`,
    ),
  updateImageModelSettings: (modelId: string, body: CodexImageModelSettings) =>
    request<CodexImageModelSettings>(
      `${base}/image-models/${encodeURIComponent(modelId)}/settings`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  getSettings: () => request<CodexSubscriptionSettings>(`${base}/settings`),
  updateSettings: (body: CodexSubscriptionSettingsUpdate) =>
    request<CodexSubscriptionSettings>(`${base}/settings`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};
