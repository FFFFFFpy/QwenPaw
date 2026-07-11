import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CodexModelManagementModal } from "./CodexModelManagementModal";

const mocks = vi.hoisted(() => ({
  getModels: vi.fn(),
  getAccount: vi.fn(),
  getChatModelSettings: vi.fn(),
  getImageModelSettings: vi.fn(),
  refreshModels: vi.fn(),
  updateChatModelSettings: vi.fn(),
  updateImageModelSettings: vi.fn(),
  setActiveLlm: vi.fn(),
  success: vi.fn(),
}));

vi.mock("../../../../../api", () => ({
  default: { setActiveLlm: mocks.setActiveLlm },
}));

vi.mock("../../../../../api/modules/codexSubscription", () => ({
  codexSubscriptionApi: {
    getModels: mocks.getModels,
    getAccount: mocks.getAccount,
    getChatModelSettings: mocks.getChatModelSettings,
    getImageModelSettings: mocks.getImageModelSettings,
    refreshModels: mocks.refreshModels,
    updateChatModelSettings: mocks.updateChatModelSettings,
    updateImageModelSettings: mocks.updateImageModelSettings,
  },
}));

vi.mock("../../../../../hooks/useAppMessage", () => ({
  useAppMessage: () => ({ message: { success: mocks.success } }),
}));

const chatModel = (id: string, name: string, active = false) => ({
  model_id: id,
  display_name: name,
  description: "订阅模型",
  kind: "chat" as const,
  availability: "unknown" as const,
  is_active: active,
  capabilities: ["text", "image_input", "tools"] as const,
  reasoning_effort: null,
  default_reasoning_effort: id.endsWith("sol") ? "low" : "medium",
  reasoning_effort_options: id.endsWith("luna")
    ? ["auto", "low", "medium", "high", "xhigh", "max"]
    : ["auto", "low", "medium", "high", "xhigh", "max", "ultra"],
  relay_reasoning: true,
  context_size: 262144,
  compact_threshold: 0.9,
  compact_trigger: 235930,
  catalog_max_output_tokens: 128000,
});

const catalog = {
  source: "bundled_compatibility_catalog" as const,
  chat_models: [
    chatModel("gpt-5.6-sol", "GPT-5.6 Sol", true),
    chatModel("gpt-5.6-luna", "GPT-5.6 Luna"),
  ],
  image_models: [
    {
      model_id: "gpt-image-2" as const,
      display_name: "GPT Image 2",
      description: "真实图片生成与编辑",
      kind: "image_generation" as const,
      availability: "unknown" as const,
      is_default: true,
      capabilities: [
        "image_generate",
        "image_edit",
        "multiple_references",
      ] as const,
      max_count: 4,
      max_input_images: 5,
      output_formats: ["png", "jpeg", "webp"] as const,
    },
  ],
};

describe("CodexModelManagementModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getModels.mockResolvedValue(catalog);
    mocks.refreshModels.mockResolvedValue(catalog);
    mocks.getAccount.mockResolvedValue({ connected: true });
    mocks.getChatModelSettings.mockResolvedValue({
      reasoning_effort: null,
      relay_reasoning: true,
    });
  });

  it("shows separate chat and image groups with accurate capabilities", async () => {
    render(
      <CodexModelManagementModal open onClose={vi.fn()} onSaved={vi.fn()} />,
    );
    expect(await screen.findByText("GPT-5.6 Sol")).toBeInTheDocument();
    expect(screen.getByText("对话模型")).toBeInTheDocument();
    expect(screen.getByText("图像生成")).toBeInTheDocument();
    const solRow = screen.getByText("GPT-5.6 Sol").closest("div[class*='row']");
    expect(solRow).not.toBeNull();
    const solRowElement = solRow as HTMLElement;
    expect(within(solRowElement).getByText("图片输入")).toBeInTheDocument();
    expect(
      within(solRowElement).queryByText("图片生成"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("GPT Image 2")).toBeInTheDocument();
    expect(screen.queryByText("添加模型")).not.toBeInTheDocument();
    expect(screen.queryByText("删除模型")).not.toBeInTheDocument();
  });

  it("filters models and dynamically limits Luna effort options", async () => {
    render(
      <CodexModelManagementModal open onClose={vi.fn()} onSaved={vi.fn()} />,
    );
    await screen.findByText("GPT-5.6 Luna");
    fireEvent.change(screen.getByLabelText("搜索模型"), {
      target: { value: "luna" },
    });
    expect(screen.queryByText("GPT-5.6 Sol")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("设置"));
    await waitFor(() => expect(mocks.getChatModelSettings).toHaveBeenCalled());
    expect(screen.getByRole("option", { name: "Max" })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "Ultra" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "自动（模型默认：Medium）" }),
    ).toBeInTheDocument();
    expect(screen.getByText("128,000 · 只读")).toBeInTheDocument();
  });

  it("reloads only the bundled catalog with accurate success copy", async () => {
    render(
      <CodexModelManagementModal open onClose={vi.fn()} onSaved={vi.fn()} />,
    );
    await screen.findByText("重新加载内置目录");
    fireEvent.click(screen.getByText("重新加载内置目录"));
    await waitFor(() => expect(mocks.refreshModels).toHaveBeenCalledTimes(1));
    expect(mocks.success).toHaveBeenCalledWith("内置兼容目录已重新加载");
  });
});
