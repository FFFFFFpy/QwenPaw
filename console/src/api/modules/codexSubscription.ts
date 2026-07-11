import { request } from "../request";
import type {
  CodexAccountStatus,
  CodexChatModelSettings,
  CodexImageModelSettings,
  CodexLoginStart,
  CodexLoginStatus,
  CodexModelsRefresh,
  CodexRateLimits,
} from "../types/codexSubscription";

const base = "/providers/openai-codex";

export const codexSubscriptionApi = {
  getAccount: () => request<CodexAccountStatus>(`${base}/account`),
  startLogin: (signal?: AbortSignal) =>
    request<CodexLoginStart>(`${base}/oauth/start`, {
      method: "POST",
      ...(signal ? { signal } : {}),
    }),
  completeLogin: (
    body: {
      callback_url?: string;
      code?: string;
      state?: string;
    },
    signal?: AbortSignal,
  ) =>
    request<CodexAccountStatus>(`${base}/oauth/complete`, {
      method: "POST",
      body: JSON.stringify(body),
      ...(signal ? { signal } : {}),
    }),
  getLoginStatus: (state: string, signal?: AbortSignal) => {
    const path = `${base}/oauth/status?state=${encodeURIComponent(state)}`;
    return signal
      ? request<CodexLoginStatus>(path, { signal })
      : request<CodexLoginStatus>(path);
  },
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
};
