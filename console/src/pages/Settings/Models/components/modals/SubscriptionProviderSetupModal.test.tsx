import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProviderInfo } from "../../../../../api/types";

const hoisted = vi.hoisted(() => ({
  api: {
    getRuntime: vi.fn(),
    redetectRuntime: vi.fn(),
    getAccount: vi.fn(),
    getRateLimits: vi.fn(),
    refreshModels: vi.fn(),
    logout: vi.fn(),
    getSettings: vi.fn(),
    updateSettings: vi.fn(),
  },
}));

vi.mock("../../../../../api/modules/codexSubscription", () => ({
  codexSubscriptionApi: hoisted.api,
}));

vi.mock("../../../../../hooks/useAppMessage", () => ({
  useAppMessage: () => ({
    message: { success: vi.fn(), error: vi.fn() },
  }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: { count?: number }) =>
      values?.count === undefined ? key : `${key}:${values.count}`,
  }),
}));

vi.mock("./CodexSubscriptionLoginModal", () => ({
  CodexSubscriptionLoginModal: () => null,
}));

vi.mock("../ProviderIconComponent", () => ({
  ProviderIcon: () => null,
}));

import { SubscriptionProviderSetupModal } from "./SubscriptionProviderSetupModal";

function subscription(
  accountState: "unknown" | "disconnected" | "connected",
): ProviderInfo {
  return {
    id: "openai-codex",
    name: "OpenAI Codex",
    api_key_prefix: "",
    chat_model: "CodexSubscriptionChatModel",
    models: [],
    extra_models: [],
    is_custom: false,
    is_local: false,
    support_model_discovery: true,
    support_connection_check: true,
    freeze_url: true,
    require_api_key: false,
    api_key: "",
    base_url: "codex-app-server://local",
    generate_kwargs: {},
    oauth_connected: accountState === "connected",
    meta: {
      provider_kind: "cloud_subscription",
      account_state: accountState,
    },
  };
}

const runtime = {
  state: "ready" as const,
  installed: true,
  binary_path: "/usr/local/bin/codex",
  binary_version: "1.0.0",
  generation_id: "generation",
  capabilities: {
    device_code_login: true,
    tool_isolation_verified: false,
  },
  error_code: null,
  message: null,
  remediation: null,
};

describe("SubscriptionProviderSetupModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hoisted.api.getRuntime.mockResolvedValue(runtime);
    hoisted.api.getRateLimits.mockResolvedValue({ primary: null });
    hoisted.api.refreshModels.mockResolvedValue({ models: [], stale: false });
  });

  const renderModal = (
    provider: ProviderInfo,
    onSaved = vi.fn().mockResolvedValue(undefined),
    onClose = vi.fn(),
  ) => {
    render(
      <SubscriptionProviderSetupModal
        provider={provider}
        open
        onClose={onClose}
        onSaved={onSaved}
        onOpenModels={vi.fn()}
      />,
    );
    return { onSaved, onClose };
  };

  it("uses the subscription setup UI without API key or base URL fields", async () => {
    renderModal(subscription("unknown"));

    expect(
      await screen.findByText("models.codexSubscription.checkAccount"),
    ).toBeInTheDocument();
    expect(screen.queryByText("models.apiKey")).not.toBeInTheDocument();
    expect(screen.queryByText("models.baseURL")).not.toBeInTheDocument();
    expect(
      screen.getByText("models.codexSubscription.toolIsolationUnverified"),
    ).toBeInTheDocument();
  });

  it("offers browser and device-code login when disconnected", async () => {
    renderModal(subscription("disconnected"));

    expect(
      await screen.findByText("models.codexSubscription.connectChatGPT"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("models.codexSubscription.deviceCode"),
    ).toBeInTheDocument();
  });

  it("refreshes providers and closes only after models were refreshed", async () => {
    const { onSaved, onClose } = renderModal(subscription("connected"));

    const refresh = await screen.findByText("models.codexSubscription.refresh");
    expect(
      screen.queryByText("models.codexSubscription.completeSetup"),
    ).not.toBeInTheDocument();

    fireEvent.click(refresh);
    fireEvent.click(
      await screen.findByText("models.codexSubscription.completeSetup"),
    );

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(hoisted.api.refreshModels).toHaveBeenCalledTimes(1);
    expect(onSaved).toHaveBeenCalledTimes(2);
  });

  it("uses the freshly checked account state for the Live badge", async () => {
    hoisted.api.getAccount.mockResolvedValue({
      connected: true,
      email_masked: "u***@example.com",
      plan_type: "Plus",
      auth_type: "chatgpt",
    });
    renderModal(subscription("unknown"));

    fireEvent.click(
      await screen.findByText("models.codexSubscription.checkAccount"),
    );

    expect(await screen.findByText("Live")).toBeInTheDocument();
  });
});
