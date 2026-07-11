import type { ModelInfo } from "./provider";

export type CodexLoginFlow = "browser";

export interface CodexAccountStatus {
  connected: boolean;
  status: "not_logged_in" | "connected" | "expiring" | "needs_login";
  email_masked: string | null;
  display_name: string | null;
  expires_at: number | null;
}

export interface CodexLoginStart {
  state: string;
  authorize_url: string;
  expires_in: number;
  manual_callback_supported: boolean;
  redirect_uri: string;
}

export interface CodexLoginStatus {
  status: "pending" | "completed" | "failed" | "expired";
  error: string | null;
  account: CodexAccountStatus | null;
}

export interface CodexModelsRefresh {
  models: ModelInfo[];
  source: "subscription_catalog";
  availability: Record<
    string,
    "unknown_until_validated" | "available" | "unavailable"
  >;
  context_size: number;
  compact_threshold: number;
  compact_trigger: number;
  max_output_tokens: number;
}

export interface CodexRateLimits {
  available: boolean;
  message: string;
}

export interface CodexSubscriptionSettings {
  transport: "direct";
  reasoning_effort: string | null;
  relay_reasoning: boolean;
  context_size: number;
  compact_threshold: number;
  direct_enabled: boolean;
}

export interface CodexSubscriptionSettingsUpdate {
  reasoning_effort?: string | null;
  relay_reasoning?: boolean;
}
