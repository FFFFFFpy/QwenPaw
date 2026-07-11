import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OAuthConfirmModal } from "./OAuthConfirmModal";

const mocks = vi.hoisted(() => ({
  startOAuth: vi.fn(),
  getOAuthStatus: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  openExternalLink: vi.fn(),
}));

vi.mock("../../../api/modules/provider", () => ({
  providerApi: {
    startOAuth: mocks.startOAuth,
    getOAuthStatus: mocks.getOAuthStatus,
  },
}));

vi.mock("../../../hooks/useAppMessage", () => ({
  useAppMessage: () => ({
    message: { success: mocks.success, error: mocks.error },
  }),
}));

vi.mock("../../../utils/openExternalLink", () => ({
  openExternalLink: mocks.openExternalLink,
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

describe("OAuthConfirmModal", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.startOAuth.mockResolvedValue({
      authorize_url: "https://auth.example/authorize",
      state: "state",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("aborts and stops polling after the modal closes", async () => {
    const { rerender } = render(
      <OAuthConfirmModal
        open
        providerId="openai-codex"
        providerName="ChatGPT"
        onSuccess={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("modelSelector.oauthContinue"));
    await act(async () => {
      await Promise.resolve();
    });
    expect(mocks.startOAuth).toHaveBeenCalled();
    const signal = mocks.startOAuth.mock.calls[0][1] as AbortSignal;
    rerender(
      <OAuthConfirmModal
        open={false}
        providerId="openai-codex"
        providerName="ChatGPT"
        onSuccess={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    await vi.advanceTimersByTimeAsync(5000);

    expect(signal.aborted).toBe(true);
    expect(mocks.getOAuthStatus).not.toHaveBeenCalled();
  });

  it("shows a non-spinning failed state", async () => {
    mocks.getOAuthStatus.mockResolvedValue({ status: "failed" });
    render(
      <OAuthConfirmModal
        open
        providerId="openai-codex"
        providerName="ChatGPT"
        onSuccess={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("modelSelector.oauthContinue"));
    await act(async () => {
      await Promise.resolve();
    });
    expect(mocks.startOAuth).toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText("modelSelector.oauthFailed")).toBeInTheDocument();

    expect(screen.queryByText("modelSelector.oauthWaiting")).toBeNull();
    expect(document.querySelector("svg[style*='animation']")).toBeNull();
  });
});
