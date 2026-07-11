import React, { useCallback, useEffect, useState } from "react";
import { Modal } from "@agentscope-ai/design";
import type { ProviderInfo } from "../../../../../api/types";
import type { CodexAccountStatus } from "../../../../../api/types/codexSubscription";
import { codexSubscriptionApi } from "../../../../../api/modules/codexSubscription";
import { useAppMessage } from "../../../../../hooks/useAppMessage";
import { ProviderIcon } from "../ProviderIconComponent";
import { CodexSubscriptionLoginModal } from "../modals/CodexSubscriptionLoginModal";
import { CodexModelManagementModal } from "../modals/CodexModelManagementModal";
import styles from "../../index.module.less";

interface Props {
  provider: ProviderInfo;
  onSaved: () => void;
  onOpenModels: (provider: ProviderInfo) => void;
}

export const SubscriptionProviderCard = React.memo(
  function SubscriptionProviderCard({ provider, onSaved }: Props) {
    const { message } = useAppMessage();
    const [account, setAccount] = useState<CodexAccountStatus | null>(null);
    const [loginOpen, setLoginOpen] = useState(false);
    const [modelsOpen, setModelsOpen] = useState(false);
    const load = useCallback(async () => {
      try {
        setAccount(await codexSubscriptionApi.getAccount());
      } catch (reason) {
        message.error(
          reason instanceof Error ? reason.message : String(reason),
        );
      }
    }, [message]);
    useEffect(() => {
      void load();
    }, [load]);

    const logout = () =>
      Modal.confirm({
        title: "退出 ChatGPT 登录",
        content: "凭据将从 QwenPaw Secret Store 安全删除。",
        okButtonProps: { danger: true },
        onOk: async () => {
          await codexSubscriptionApi.logout();
          await load();
          onSaved();
        },
      });

    return (
      <div
        className={styles.groupCardGlass}
        data-testid="subscription-provider-card"
      >
        <div className={styles.groupCardHeader}>
          <ProviderIcon providerId={provider.id} size={36} />
          <span className={styles.groupCardName}>
            OpenAI ChatGPT/Codex 订阅
          </span>
          <span className={styles.customTag}>兼容性接入</span>
          {account?.connected && (
            <div className={styles.groupCardLiveBadge}>
              <span className={styles.groupCardPulse} /> 已登录
            </div>
          )}
        </div>
        <div className={styles.groupCardContent}>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>账户</span>
            <span className={styles.groupCardFieldValue}>
              {account?.email_masked || "未登录"}
            </span>
          </div>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>模型来源</span>
            <span className={styles.groupCardFieldValue}>内置兼容目录</span>
          </div>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>模型可用性</span>
            <span className={styles.groupCardFieldValue}>
              首次调用时由账户权限验证
            </span>
          </div>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>工作上下文</span>
            <span className={styles.groupCardFieldValue}>
              256K · 90% 压缩 · 235,930 触发
            </span>
          </div>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>最大输出能力</span>
            <span className={styles.groupCardFieldValue}>128K（只读）</span>
          </div>
          <div className={styles.groupCardMono}>
            使用 ChatGPT/Codex 非公开兼容性接口；上游可能变更，不属于 OpenAI
            Platform 公共稳定 API。
          </div>
        </div>
        <div className={styles.groupCardActions}>
          {!account?.connected ? (
            <button
              className={styles.groupCardActBtn}
              onClick={() => setLoginOpen(true)}
            >
              登录 ChatGPT
            </button>
          ) : (
            <>
              <button
                className={`${styles.groupCardActBtn} ${styles.groupCardActBtnDanger}`}
                onClick={logout}
              >
                退出登录
              </button>
            </>
          )}
          <button
            className={styles.groupCardActBtn}
            onClick={() => setModelsOpen(true)}
          >
            模型管理
          </button>
        </div>
        <CodexSubscriptionLoginModal
          open={loginOpen}
          onConnected={() => {
            setLoginOpen(false);
            void load();
            onSaved();
          }}
          onClose={() => setLoginOpen(false)}
        />
        <CodexModelManagementModal
          open={modelsOpen}
          onClose={() => setModelsOpen(false)}
          onSaved={onSaved}
        />
      </div>
    );
  },
);
