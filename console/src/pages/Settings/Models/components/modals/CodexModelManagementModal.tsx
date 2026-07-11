import { useCallback, useEffect, useMemo, useState } from "react";
import { Modal } from "@agentscope-ai/design";
import api from "../../../../../api";
import { codexSubscriptionApi } from "../../../../../api/modules/codexSubscription";
import type {
  CodexAccountStatus,
  CodexChatModel,
  CodexChatModelSettings,
  CodexImageModel,
  CodexImageModelSettings,
  CodexModelsRefresh,
} from "../../../../../api/types/codexSubscription";
import { useAppMessage } from "../../../../../hooks/useAppMessage";
import styles from "./CodexModelManagementModal.module.less";

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

type SelectedModel =
  | { kind: "chat"; model: CodexChatModel }
  | { kind: "image"; model: CodexImageModel };

const availabilityText = {
  unknown: "未验证",
  available: "可用",
  unavailable: "不可用",
};

const capabilityText: Record<string, string> = {
  text: "文本",
  image_input: "图片输入",
  tools: "工具调用",
  image_generate: "图片生成",
  image_edit: "图片编辑",
  multiple_references: "多参考图",
};

const effortLabel = (value: string) =>
  value === "xhigh" ? "XHigh" : value.charAt(0).toUpperCase() + value.slice(1);

const imageOptionLabel = (value: string) => {
  if (/^\d+x\d+$/.test(value)) return value.replace("x", "×");
  if (value === "png") return "PNG";
  if (value === "jpeg") return "JPEG";
  if (value === "webp") return "WebP";
  return value.charAt(0).toUpperCase() + value.slice(1);
};

export function CodexModelManagementModal({ open, onClose, onSaved }: Props) {
  const { message } = useAppMessage();
  const [catalog, setCatalog] = useState<CodexModelsRefresh | null>(null);
  const [account, setAccount] = useState<CodexAccountStatus | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<SelectedModel | null>(null);
  const [chatSettings, setChatSettings] =
    useState<CodexChatModelSettings | null>(null);
  const [imageSettings, setImageSettings] =
    useState<CodexImageModelSettings | null>(null);

  const load = useCallback(async () => {
    const [models, status] = await Promise.all([
      codexSubscriptionApi.getModels(),
      codexSubscriptionApi.getAccount(),
    ]);
    setCatalog(models);
    setAccount(status);
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [load, open]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    const includes = (model: { model_id: string; display_name: string }) =>
      !query ||
      model.model_id.toLowerCase().includes(query) ||
      model.display_name.toLowerCase().includes(query);
    return {
      chat: (catalog?.chat_models ?? []).filter(includes),
      image: (catalog?.image_models ?? []).filter(includes),
    };
  }, [catalog, search]);

  const openSettings = async (selection: SelectedModel) => {
    setSelected(selection);
    if (selection.kind === "chat") {
      setChatSettings(
        await codexSubscriptionApi.getChatModelSettings(
          selection.model.model_id,
        ),
      );
    } else {
      setImageSettings(
        await codexSubscriptionApi.getImageModelSettings(
          selection.model.model_id,
        ),
      );
    }
  };

  const setActive = async (model: CodexChatModel) => {
    setBusy(true);
    try {
      await api.setActiveLlm({
        provider_id: "openai-codex",
        model: model.model_id,
        scope: "global",
      });
      message.success(`已将 ${model.display_name} 设为当前模型`);
      await load();
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  const saveSettings = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      if (selected.kind === "chat" && chatSettings) {
        await codexSubscriptionApi.updateChatModelSettings(
          selected.model.model_id,
          chatSettings,
        );
      } else if (selected.kind === "image" && imageSettings) {
        await codexSubscriptionApi.updateImageModelSettings(
          selected.model.model_id,
          imageSettings,
        );
      }
      message.success("模型设置已保存");
      setSelected(null);
      await load();
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  const renderRow = (
    model: CodexChatModel | CodexImageModel,
    kind: "chat" | "image",
  ) => (
    <div className={styles.row} key={model.model_id}>
      <div className={styles.identity}>
        <div className={styles.name}>{model.display_name}</div>
        <div className={styles.modelId}>{model.model_id}</div>
        <div className={styles.description}>{model.description}</div>
      </div>
      <div className={styles.tags}>
        {model.capabilities.map((capability) => (
          <span className={styles.tag} key={capability}>
            {capabilityText[capability]}
          </span>
        ))}
        <span className={styles.tag}>订阅</span>
        <span className={styles.tag}>内置目录</span>
        <span className={styles.tag}>
          {availabilityText[model.availability]}
        </span>
        {kind === "chat" && (model as CodexChatModel).is_active && (
          <span className={`${styles.tag} ${styles.active}`}>当前使用</span>
        )}
        {kind === "image" && <span className={styles.tag}>默认生图模型</span>}
      </div>
      <div className={styles.actions}>
        {kind === "chat" && !(model as CodexChatModel).is_active && (
          <button
            className={styles.button}
            disabled={busy}
            onClick={() => void setActive(model as CodexChatModel)}
          >
            设为当前模型
          </button>
        )}
        <button
          className={styles.button}
          onClick={() =>
            void openSettings(
              kind === "chat"
                ? { kind, model: model as CodexChatModel }
                : { kind, model: model as CodexImageModel },
            )
          }
        >
          设置
        </button>
      </div>
    </div>
  );

  return (
    <>
      <Modal
        open={open}
        title="OpenAI ChatGPT/Codex 订阅 — 模型管理"
        onCancel={onClose}
        footer={null}
        width={920}
      >
        <div className={styles.content}>
          <div className={styles.topline}>
            <input
              aria-label="搜索模型"
              className={styles.search}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索模型…"
            />
            <span>{account?.connected ? "账户已登录" : "账户未登录"}</span>
          </div>
          <div className={styles.notice}>
            使用 ChatGPT/Codex 非公开兼容性接口；上游可能变更，不属于 OpenAI
            Platform 公共稳定 API。
          </div>
          <section>
            <h4 className={styles.groupTitle}>对话模型</h4>
            <div className={styles.list}>
              {filtered.chat.map((model) => renderRow(model, "chat"))}
            </div>
          </section>
          <section>
            <h4 className={styles.groupTitle}>图像生成</h4>
            <div className={styles.list}>
              {filtered.image.map((model) => renderRow(model, "image"))}
            </div>
          </section>
          <div className={styles.footer}>
            <button className={styles.button} onClick={onClose}>
              关闭
            </button>
            <button
              className={`${styles.button} ${styles.primary}`}
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  setCatalog(await codexSubscriptionApi.refreshModels());
                  message.success("内置兼容目录已重新加载");
                } finally {
                  setBusy(false);
                }
              }}
            >
              重新加载内置目录
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        open={selected !== null}
        title={selected ? `${selected.model.display_name} — 设置` : "模型设置"}
        onCancel={() => setSelected(null)}
        footer={null}
        width={560}
      >
        {selected?.kind === "chat" && chatSettings && (
          <div className={styles.settingsGrid}>
            <div>
              <strong>{selected.model.display_name}</strong>
              <div className={styles.modelId}>{selected.model.model_id}</div>
              <div className={styles.tags}>
                <span className={styles.tag}>
                  {availabilityText[selected.model.availability]}
                </span>
                {selected.model.is_active && (
                  <span className={`${styles.tag} ${styles.active}`}>
                    当前使用
                  </span>
                )}
              </div>
            </div>
            <label className={styles.field}>
              智能程度
              <select
                value={chatSettings.reasoning_effort ?? "auto"}
                onChange={(event) =>
                  setChatSettings({
                    ...chatSettings,
                    reasoning_effort:
                      event.target.value === "auto" ? null : event.target.value,
                  })
                }
              >
                {selected.model.reasoning_effort_options.map((value) => (
                  <option value={value} key={value}>
                    {value === "auto"
                      ? `自动（模型默认：${effortLabel(
                          selected.model.default_reasoning_effort,
                        )}）`
                      : effortLabel(value)}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.field}>
              <span>
                <input
                  type="checkbox"
                  checked={chatSettings.relay_reasoning}
                  onChange={(event) =>
                    setChatSettings({
                      ...chatSettings,
                      relay_reasoning: event.target.checked,
                    })
                  }
                />{" "}
                显示推理摘要
              </span>
            </label>
            <div className={styles.capabilities}>
              <div className={styles.capability}>
                <strong>工作上下文</strong>256K（262,144）
              </div>
              <div className={styles.capability}>
                <strong>压缩阈值</strong>90% · 235,930
              </div>
              <div className={styles.capability}>
                <strong>最大输出能力</strong>128,000 · 只读
              </div>
              <div className={styles.capability}>
                <strong>输入模态</strong>文本、图片
              </div>
            </div>
            <div className={styles.footer}>
              <button
                className={styles.button}
                onClick={() =>
                  setChatSettings({
                    reasoning_effort: null,
                    relay_reasoning: true,
                  })
                }
              >
                恢复此模型默认
              </button>
              <button
                className={styles.button}
                onClick={() => setSelected(null)}
              >
                取消
              </button>
              <button
                className={`${styles.button} ${styles.primary}`}
                disabled={busy}
                onClick={() => void saveSettings()}
              >
                保存
              </button>
            </div>
          </div>
        )}

        {selected?.kind === "image" && imageSettings && (
          <div className={styles.settingsGrid}>
            {(
              [
                ["size", "默认尺寸", ["1024x1024", "1536x1024", "1024x1536"]],
                ["quality", "默认质量", ["auto", "low", "medium", "high"]],
                ["output_format", "默认输出格式", ["png", "jpeg", "webp"]],
                ["background", "默认背景", ["auto", "opaque", "transparent"]],
              ] as const
            ).map(([key, label, options]) => (
              <label className={styles.field} key={key}>
                {label}
                <select
                  value={imageSettings[key]}
                  onChange={(event) =>
                    setImageSettings({
                      ...imageSettings,
                      [key]: event.target.value,
                    })
                  }
                >
                  {options.map((option) => (
                    <option value={option} key={option}>
                      {imageOptionLabel(option)}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            <label className={styles.field}>
              单次生成数量
              <select
                value={imageSettings.count}
                onChange={(event) =>
                  setImageSettings({
                    ...imageSettings,
                    count: Number(event.target.value),
                  })
                }
              >
                {[1, 2, 3, 4].map((count) => (
                  <option value={count} key={count}>
                    {count}
                  </option>
                ))}
              </select>
            </label>
            <div className={styles.footer}>
              <button
                className={styles.button}
                onClick={() => setSelected(null)}
              >
                取消
              </button>
              <button
                className={`${styles.button} ${styles.primary}`}
                disabled={busy}
                onClick={() => void saveSettings()}
              >
                保存
              </button>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
