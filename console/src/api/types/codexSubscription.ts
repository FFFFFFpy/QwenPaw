import type { ModelInfo } from "./provider";

export type CodexRuntimeState =
  | "not_installed"
  | "stopped"
  | "starting"
  | "ready"
  | "crashed"
  | "incompatible"
  | "stopping";

export interface CodexRuntimeStatus {
  state: CodexRuntimeState;
  installed: boolean;
  binary_path: string | null;
  binary_version: string | null;
  generation_id: string | null;
  capabilities: Record<string, boolean | string | null> | null;
  error_code: string | null;
  message: string | null;
  remediation: string | null;
}

export interface CodexAccountStatus {
  connected: boolean;
  email_masked: string | null;
  plan_type: string | null;
  auth_type: string | null;
}

export interface CodexRateLimitWindow {
  used_percent: number;
  window_duration_mins: number | null;
  resets_at: number | null;
}

export interface CodexCredits {
  has_credits: boolean;
  unlimited: boolean;
  balance: string | null;
}

export interface CodexRateLimits {
  limit_id: string | null;
  limit_name: string | null;
  plan_type: string | null;
  primary: CodexRateLimitWindow | null;
  secondary: CodexRateLimitWindow | null;
  credits: CodexCredits | null;
  updated_at: number;
}

export type CodexLoginFlow = "browser" | "device_code";

export interface CodexLoginStart {
  state: string;
  flow_type: "browser_redirect" | "device_code";
  authorize_url: string | null;
  verification_url: string | null;
  user_code: string | null;
  expires_at: number;
}

export interface CodexLoginStatus {
  status: "pending" | "completed" | "failed" | "expired" | "cancelled";
  error: string | null;
  account: CodexAccountStatus | null;
}

export interface CodexModelsRefresh {
  models: ModelInfo[];
  stale: boolean;
}

export interface CodexSubscriptionSettings {
  binary_path: string;
  preferred_login_flow: CodexLoginFlow;
  tool_wait_timeout_seconds: number;
}

export interface CodexSubscriptionSettingsUpdate {
  binary_path?: string;
  preferred_login_flow?: CodexLoginFlow;
  tool_wait_timeout_seconds?: number;
}
