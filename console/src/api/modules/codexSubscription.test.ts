import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { codexSubscriptionApi } from "./codexSubscription";

vi.mock("@/api/request", () => ({ request: vi.fn() }));
import { request } from "@/api/request";

describe("codexSubscriptionApi", () => {
  beforeEach(() => vi.mocked(request).mockResolvedValue(undefined));
  afterEach(() => vi.clearAllMocks());

  it("starts browser login without passing credentials", async () => {
    await codexSubscriptionApi.startLogin();
    expect(request).toHaveBeenCalledWith(
      "/providers/openai-codex/oauth/start",
      {
        method: "POST",
      },
    );
  });

  it("encodes opaque login state when polling", async () => {
    await codexSubscriptionApi.getLoginStatus("state/with spaces");
    expect(request).toHaveBeenCalledWith(
      "/providers/openai-codex/oauth/status?state=state%2Fwith%20spaces",
    );
  });

  it("updates only non-secret subscription settings", async () => {
    const body = {
      reasoning_effort: "high",
      relay_reasoning: true,
    };
    await codexSubscriptionApi.updateSettings(body);
    expect(request).toHaveBeenCalledWith("/providers/openai-codex/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    expect(JSON.stringify(body).toLowerCase()).not.toContain("token");
  });

  it("updates one chat model without generic request kwargs", async () => {
    const body = { reasoning_effort: "high", relay_reasoning: true };
    await codexSubscriptionApi.updateChatModelSettings("gpt-5.6/luna", body);
    expect(request).toHaveBeenCalledWith(
      "/providers/openai-codex/models/gpt-5.6%2Fluna/settings",
      { method: "PUT", body: JSON.stringify(body) },
    );
    expect(JSON.stringify(body)).not.toContain("max_tokens");
  });
});
