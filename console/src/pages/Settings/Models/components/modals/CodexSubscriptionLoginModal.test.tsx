import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CodexSubscriptionLoginModal } from "./CodexSubscriptionLoginModal";

const mocks = vi.hoisted(() => ({
  startLogin: vi.fn(),
  getLoginStatus: vi.fn(),
  completeLogin: vi.fn(),
  success: vi.fn(),
  openExternalLink: vi.fn(),
}));

vi.mock("../../../../../api/modules/codexSubscription", () => ({
  codexSubscriptionApi: {
    startLogin: mocks.startLogin,
    getLoginStatus: mocks.getLoginStatus,
    completeLogin: mocks.completeLogin,
  },
}));

vi.mock("../../../../../hooks/useAppMessage", () => ({
  useAppMessage: () => ({ message: { success: mocks.success } }),
}));

vi.mock("../../../../../utils/openExternalLink", () => ({
  openExternalLink: mocks.openExternalLink,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function props(open = true) {
  return {
    open,
    onConnected: vi.fn(),
    onClose: vi.fn(),
  };
}

async function settleStart() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("CodexSubscriptionLoginModal", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.startLogin.mockResolvedValue({
      state: "oauth-state",
      authorize_url: "https://auth.example/authorize",
    });
    mocks.completeLogin.mockResolvedValue({ connected: true });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("aborts an in-flight poll when the modal closes", async () => {
    const status = deferred<{ status: "pending" }>();
    mocks.getLoginStatus.mockReturnValue(status.promise);
    const initial = props();
    const { rerender } = render(<CodexSubscriptionLoginModal {...initial} />);
    await settleStart();
    vi.advanceTimersByTime(1000);
    const signal = mocks.getLoginStatus.mock.calls[0][1] as AbortSignal;

    rerender(<CodexSubscriptionLoginModal {...initial} open={false} />);

    expect(signal.aborted).toBe(true);
  });

  it("does not schedule another poll when an old request returns", async () => {
    const status = deferred<{ status: "pending" }>();
    mocks.getLoginStatus.mockReturnValue(status.promise);
    const initial = props();
    const { rerender } = render(<CodexSubscriptionLoginModal {...initial} />);
    await settleStart();
    vi.advanceTimersByTime(1000);
    rerender(<CodexSubscriptionLoginModal {...initial} open={false} />);

    await act(async () => {
      status.resolve({ status: "pending" });
      await Promise.resolve();
    });
    vi.advanceTimersByTime(10000);

    expect(mocks.getLoginStatus).toHaveBeenCalledTimes(1);
  });

  it("stops polling and shows CircleX after failed", async () => {
    mocks.getLoginStatus.mockResolvedValue({
      status: "failed",
      error: "登录失败",
    });
    render(<CodexSubscriptionLoginModal {...props()} />);
    await settleStart();
    vi.advanceTimersByTime(1000);
    await act(async () => {
      await Promise.resolve();
    });
    vi.advanceTimersByTime(10000);

    expect(mocks.getLoginStatus).toHaveBeenCalledTimes(1);
    expect(screen.getByText("登录失败")).toBeInTheDocument();
    expect(document.querySelector("svg[style*='animation']")).toBeNull();
  });

  it("stops polling after expired", async () => {
    mocks.getLoginStatus.mockResolvedValue({ status: "expired" });
    render(<CodexSubscriptionLoginModal {...props()} />);
    await settleStart();
    vi.advanceTimersByTime(1000);
    await act(async () => {
      await Promise.resolve();
    });
    vi.advanceTimersByTime(10000);

    expect(mocks.getLoginStatus).toHaveBeenCalledTimes(1);
    expect(screen.getByText("登录请求已过期，请重新开始")).toBeInTheDocument();
    expect(document.querySelector("svg[style*='animation']")).toBeNull();
  });

  it("does not inherit an old poll when reopened", async () => {
    const oldStatus = deferred<{ status: "pending" }>();
    mocks.startLogin
      .mockResolvedValueOnce({
        state: "old-state",
        authorize_url: "https://auth.example/old",
      })
      .mockResolvedValueOnce({
        state: "new-state",
        authorize_url: "https://auth.example/new",
      });
    mocks.getLoginStatus.mockImplementation((oauthState: string) =>
      oauthState === "old-state"
        ? oldStatus.promise
        : Promise.resolve({ status: "pending" }),
    );
    const initial = props();
    const { rerender } = render(<CodexSubscriptionLoginModal {...initial} />);
    await settleStart();
    vi.advanceTimersByTime(1000);

    rerender(<CodexSubscriptionLoginModal {...initial} open={false} />);
    rerender(<CodexSubscriptionLoginModal {...initial} open />);
    await settleStart();
    await act(async () => {
      oldStatus.resolve({ status: "pending" });
      await Promise.resolve();
    });
    vi.advanceTimersByTime(1000);
    await act(async () => {
      await Promise.resolve();
    });

    const states = mocks.getLoginStatus.mock.calls.map(([value]) => value);
    expect(states.filter((value) => value === "old-state")).toHaveLength(1);
    expect(states.filter((value) => value === "new-state")).toHaveLength(1);
  });

  it("still completes login from a manually pasted callback URL", async () => {
    mocks.getLoginStatus.mockResolvedValue({ status: "pending" });
    const callbacks = props();
    render(<CodexSubscriptionLoginModal {...callbacks} />);
    await settleStart();
    fireEvent.change(
      screen.getByPlaceholderText(
        "http://localhost:1455/auth/callback?code=...&state=...",
      ),
      {
        target: {
          value:
            "http://localhost:1455/auth/callback?code=code&state=oauth-state",
        },
      },
    );
    fireEvent.click(screen.getByText("完成登录"));
    await act(async () => {
      await Promise.resolve();
    });

    expect(mocks.completeLogin).toHaveBeenCalledWith(
      {
        callback_url:
          "http://localhost:1455/auth/callback?code=code&state=oauth-state",
        state: "oauth-state",
      },
      expect.any(AbortSignal),
    );
    expect(callbacks.onConnected).toHaveBeenCalledTimes(1);
  });
});
