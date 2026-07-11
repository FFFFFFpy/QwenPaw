import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProviderInfo } from "../../../api/types/provider";

const hoisted = vi.hoisted(() => ({
  providers: [] as ProviderInfo[],
  fetchAll: vi.fn(),
}));

vi.mock("./useProviders", () => ({
  useProviders: () => ({
    providers: hoisted.providers,
    activeModels: null,
    loading: false,
    error: null,
    fetchAll: hoisted.fetchAll,
  }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/components/PageHeader", () => ({
  PageHeader: () => null,
}));

vi.mock("./components/ProviderIconComponent", () => ({
  ProviderIcon: () => null,
}));

vi.mock("./components", () => ({
  LoadingState: () => null,
  ProviderCard: () => null,
  ProviderGroupCard: () => null,
  CustomProviderModal: () => null,
  ModelsSection: () => null,
  ModelManageModal: () => null,
  ProviderConfigModal: ({ provider }: { provider: ProviderInfo }) => (
    <div data-testid="provider-config-modal">{provider.id}</div>
  ),
  SubscriptionProviderSetupModal: ({
    provider,
  }: {
    provider: ProviderInfo;
  }) => <div data-testid="subscription-setup-modal">{provider.id}</div>,
}));

import ModelsPage from "./index";

function provider(overrides: Partial<ProviderInfo>): ProviderInfo {
  return {
    id: "provider",
    name: "Provider",
    api_key_prefix: "",
    chat_model: "",
    models: [],
    extra_models: [],
    is_custom: false,
    is_local: false,
    support_model_discovery: false,
    support_connection_check: false,
    freeze_url: false,
    require_api_key: true,
    api_key: "",
    base_url: "",
    generate_kwargs: {},
    ...overrides,
  };
}

describe("ModelsPage available-provider setup routing", () => {
  beforeEach(() => {
    localStorage.clear();
    hoisted.fetchAll.mockReset();
    hoisted.providers = [];
  });

  const renderPage = () =>
    render(
      <MemoryRouter>
        <ModelsPage />
      </MemoryRouter>,
    );

  it("opens the API provider config modal for an ordinary provider", () => {
    hoisted.providers = [provider({ id: "ordinary", name: "Ordinary" })];
    renderPage();

    fireEvent.click(screen.getByText("Ordinary"));

    expect(screen.getByTestId("provider-config-modal")).toHaveTextContent(
      "ordinary",
    );
    expect(
      screen.queryByTestId("subscription-setup-modal"),
    ).not.toBeInTheDocument();
  });

  it("opens the dedicated setup modal for OpenAI Codex", () => {
    hoisted.providers = [
      provider({
        id: "openai",
        name: "OpenAI",
        provider_group: "openai",
        provider_group_name: "OpenAI",
      }),
      provider({
        id: "openai-codex",
        name: "OpenAI Codex",
        provider_group: "openai",
        provider_group_name: "OpenAI",
        require_api_key: false,
        oauth_connected: false,
        meta: {
          provider_kind: "cloud_subscription",
          account_state: "unknown",
        },
      }),
    ];
    renderPage();

    fireEvent.click(screen.getByText("OpenAI Codex"));

    expect(screen.getByTestId("subscription-setup-modal")).toHaveTextContent(
      "openai-codex",
    );
    expect(
      screen.queryByTestId("provider-config-modal"),
    ).not.toBeInTheDocument();
  });
});
