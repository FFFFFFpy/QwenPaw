import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Input, Modal } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import type { ProviderInfo } from "../../../../../api/types";
import type {
  CodexAccountStatus,
  CodexLoginFlow,
  CodexRateLimits,
  CodexRuntimeStatus,
  CodexSubscriptionSettings,
} from "../../../../../api/types/codexSubscription";
import { codexSubscriptionApi } from "../../../../../api/modules/codexSubscription";
import { useAppMessage } from "../../../../../hooks/useAppMessage";
import { ProviderIcon } from "../ProviderIconComponent";
import { CodexSubscriptionLoginModal } from "../modals/CodexSubscriptionLoginModal";
import styles from "../../index.module.less";

interface Props {
  provider: ProviderInfo;
  onSaved: () => void;
  onOpenModels: (provider: ProviderInfo) => void;
}

export const SubscriptionProviderCard = React.memo(
  function SubscriptionProviderCard({
    provider,
    onSaved,
    onOpenModels,
  }: Props) {
    const { t } = useTranslation();
    const { message } = useAppMessage();
    const [runtime, setRuntime] = useState<CodexRuntimeStatus | null>(null);
    const [account, setAccount] = useState<CodexAccountStatus | null>(null);
    const [limits, setLimits] = useState<CodexRateLimits | null>(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [loginFlow, setLoginFlow] = useState<CodexLoginFlow | null>(null);
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [settings, setSettings] = useState<CodexSubscriptionSettings | null>(
      null,
    );

    const load = useCallback(
      async (readAccount = false) => {
        setLoading(true);
        try {
          const runtimeResult = await codexSubscriptionApi.getRuntime();
          setRuntime(runtimeResult);
          if (runtimeResult.state === "ready" && readAccount) {
            try {
              const accountResult = await codexSubscriptionApi.getAccount();
              setAccount(accountResult);
              if (accountResult.connected) {
                try {
                  setLimits(await codexSubscriptionApi.getRateLimits());
                } catch {
                  setLimits(null);
                }
              } else {
                setLimits(null);
              }
            } catch (reason) {
              message.error(
                reason instanceof Error ? reason.message : String(reason),
              );
            }
          }
        } catch (reason) {
          setRuntime(
            (current) =>
              current || {
                state: "crashed",
                installed: true,
                binary_path: null,
                binary_version: null,
                generation_id: null,
                capabilities: null,
                error_code: "CODEX_RUNTIME_START_FAILED",
                message:
                  reason instanceof Error ? reason.message : String(reason),
                remediation: null,
              },
          );
        } finally {
          setLoading(false);
        }
      },
      [message],
    );

    useEffect(() => {
      void load(false);
    }, [load]);

    const totalModels = provider.models.length + provider.extra_models.length;
    const isLive = runtime?.state === "ready" && account?.connected === true;
    const usage = useMemo(() => {
      const primary = limits?.primary;
      if (!primary) return "—";
      const percent = `${Math.round(primary.used_percent)}%`;
      if (!primary.resets_at) return percent;
      return `${percent} · ${new Date(
        primary.resets_at * 1000,
      ).toLocaleString()}`;
    }, [limits]);

    const refresh = async () => {
      setBusy(true);
      try {
        await codexSubscriptionApi.refreshModels();
        message.success(t("models.codexSubscription.modelsRefreshed"));
        onSaved();
        await load(true);
      } catch (reason) {
        message.error(
          reason instanceof Error
            ? reason.message
            : t("models.codexSubscription.actionFailed"),
        );
      } finally {
        setBusy(false);
      }
    };

    const redetect = async () => {
      setBusy(true);
      try {
        setRuntime(await codexSubscriptionApi.redetectRuntime());
        await load(false);
      } finally {
        setBusy(false);
      }
    };

    const signOut = () => {
      Modal.confirm({
        title: t("models.codexSubscription.signOut"),
        content: t("models.codexSubscription.signOutConfirm"),
        okButtonProps: { danger: true },
        onOk: async () => {
          await codexSubscriptionApi.logout();
          setAccount({
            connected: false,
            email_masked: null,
            plan_type: null,
            auth_type: null,
          });
          setLimits(null);
          onSaved();
        },
      });
    };

    const openSettings = async () => {
      try {
        setSettings(await codexSubscriptionApi.getSettings());
        setSettingsOpen(true);
      } catch (reason) {
        message.error(
          reason instanceof Error ? reason.message : String(reason),
        );
      }
    };

    const saveSettings = async () => {
      if (!settings) return;
      setBusy(true);
      try {
        await codexSubscriptionApi.updateSettings(settings);
        setSettingsOpen(false);
        await redetect();
      } finally {
        setBusy(false);
      }
    };

    return (
      <div
        className={styles.groupCardGlass}
        data-testid="subscription-provider-card"
      >
        <div className={styles.groupCardHeader}>
          <ProviderIcon providerId={provider.id} size={36} />
          <span className={styles.groupCardName}>{provider.name}</span>
          <span className={styles.customTag}>
            {t("models.codexSubscription.cloudSubscription")}
          </span>
          {isLive && (
            <div className={styles.groupCardLiveBadge}>
              <span className={styles.groupCardPulse} /> Live
            </div>
          )}
        </div>

        <div className={styles.groupCardContent} aria-busy={loading}>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>
              {t("models.codexSubscription.runtime")}
            </span>
            <span className={styles.groupCardFieldValue}>
              {t(
                `models.codexSubscription.runtimeStates.${
                  runtime?.state || "starting"
                }`,
              )}
            </span>
          </div>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>
              {t("models.codexSubscription.account")}
            </span>
            <span className={styles.groupCardFieldValue}>
              {account?.email_masked ||
                t("models.codexSubscription.notConnected")}
            </span>
          </div>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>
              {t("models.codexSubscription.plan")}
            </span>
            <span className={styles.groupCardFieldValue}>
              {account?.plan_type || "—"}
            </span>
          </div>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>
              {t("models.codexSubscription.modelsLabel")}
            </span>
            <span className={styles.groupCardFieldValue}>{totalModels}</span>
          </div>
          <div className={styles.groupCardField}>
            <span className={styles.groupCardFieldLabel}>
              {t("models.codexSubscription.usage")}
            </span>
            <span className={styles.groupCardFieldValue}>{usage}</span>
          </div>
          {runtime?.message && (
            <div className={styles.groupCardMono}>{runtime.message}</div>
          )}
          {runtime?.remediation && (
            <div className={styles.groupCardMono}>{runtime.remediation}</div>
          )}
          {runtime?.state === "not_installed" && (
            <div className={styles.groupCardMono}>
              {t("models.codexSubscription.installHint")}
            </div>
          )}
        </div>

        <div className={styles.groupCardActions}>
          {runtime?.state === "ready" && !account?.connected && (
            <>
              {account === null && (
                <button
                  className={styles.groupCardActBtn}
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await load(true);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  {t("models.codexSubscription.checkAccount")}
                </button>
              )}
              <button
                className={styles.groupCardActBtn}
                onClick={() => setLoginFlow("browser")}
              >
                {t("models.codexSubscription.connectChatGPT")}
              </button>
              {runtime.capabilities?.device_code_login !== false && (
                <button
                  className={styles.groupCardActBtn}
                  onClick={() => setLoginFlow("device_code")}
                >
                  {t("models.codexSubscription.deviceCode")}
                </button>
              )}
            </>
          )}
          {runtime?.state !== "ready" && (
            <button
              className={styles.groupCardActBtn}
              disabled={busy}
              onClick={redetect}
            >
              {t("models.codexSubscription.redetect")}
            </button>
          )}
          {account?.connected && (
            <>
              <button
                className={styles.groupCardActBtn}
                disabled={busy}
                onClick={refresh}
              >
                {t("models.codexSubscription.refresh")}
              </button>
              <button
                className={styles.groupCardActBtn}
                onClick={() => onOpenModels(provider)}
              >
                {t("models.models")}
              </button>
              <button
                className={`${styles.groupCardActBtn} ${styles.groupCardActBtnDanger}`}
                onClick={signOut}
              >
                {t("models.codexSubscription.signOut")}
              </button>
            </>
          )}
          <button className={styles.groupCardActBtn} onClick={openSettings}>
            {t("models.settings")}
          </button>
        </div>

        <CodexSubscriptionLoginModal
          open={loginFlow !== null}
          flow={loginFlow || "browser"}
          onConnected={() => {
            setLoginFlow(null);
            onSaved();
            void load(true);
          }}
          onClose={() => setLoginFlow(null)}
        />

        <Modal
          open={settingsOpen}
          title={t("models.codexSubscription.settingsTitle")}
          onCancel={() => setSettingsOpen(false)}
          onOk={saveSettings}
          confirmLoading={busy}
        >
          {settings && (
            <div style={{ display: "grid", gap: 14 }}>
              <label>
                <div>{t("models.codexSubscription.binaryPath")}</div>
                <Input
                  value={settings.binary_path}
                  onChange={(event) =>
                    setSettings({
                      ...settings,
                      binary_path: event.target.value,
                    })
                  }
                  placeholder="/absolute/path/to/codex"
                />
              </label>
              <label>
                <div>{t("models.codexSubscription.loginPreference")}</div>
                <select
                  value={settings.preferred_login_flow}
                  onChange={(event) =>
                    setSettings({
                      ...settings,
                      preferred_login_flow: event.target
                        .value as CodexLoginFlow,
                    })
                  }
                >
                  <option value="browser">
                    {t("models.codexSubscription.browser")}
                  </option>
                  <option value="device_code">
                    {t("models.codexSubscription.deviceCode")}
                  </option>
                </select>
              </label>
              <label>
                <div>{t("models.codexSubscription.toolTimeout")}</div>
                <Input
                  type="number"
                  value={settings.tool_wait_timeout_seconds}
                  onChange={(event) =>
                    setSettings({
                      ...settings,
                      tool_wait_timeout_seconds: Number(event.target.value),
                    })
                  }
                />
              </label>
            </div>
          )}
        </Modal>
      </div>
    );
  },
);
