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
  source: "bundled_compatibility_catalog";
  chat_models: CodexChatModel[];
  image_models: CodexImageModel[];
}

export type CodexAvailability = "unknown" | "available" | "unavailable";

export interface CodexChatModel {
  model_id: string;
  display_name: string;
  description: string;
  kind: "chat";
  availability: CodexAvailability;
  is_active: boolean;
  capabilities: Array<"text" | "image_input" | "tools">;
  reasoning_effort: string | null;
  default_reasoning_effort: string;
  reasoning_effort_options: string[];
  relay_reasoning: boolean;
  context_size: number;
  compact_threshold: number;
  compact_trigger: number;
  catalog_max_output_tokens: number;
}

export interface CodexImageModel {
  model_id: "gpt-image-2";
  display_name: string;
  description: string;
  kind: "image_generation";
  availability: CodexAvailability;
  is_default: boolean;
  capabilities: Array<"image_generate" | "image_edit" | "multiple_references">;
  max_count: number;
  max_input_images: number;
  output_formats: Array<"png" | "jpeg" | "webp">;
}

export interface CodexChatModelSettings {
  reasoning_effort: string | null;
  relay_reasoning: boolean;
}

export interface CodexImageModelSettings {
  size: string;
  quality: "auto" | "low" | "medium" | "high";
  output_format: "png" | "jpeg" | "webp";
  background: "auto" | "opaque" | "transparent";
  count: number;
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
