import { useState, useEffect, useCallback, useRef } from "react";
import { Modal, Button } from "@agentscope-ai/design";
import { CircleX, Loader2, ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { providerApi } from "../../../api/modules/provider";
import { useAppMessage } from "../../../hooks/useAppMessage";
import { openExternalLink } from "../../../utils/openExternalLink";

interface OAuthConfirmModalProps {
  open: boolean;
  providerId: string;
  providerName: string;
  onSuccess: () => void;
  onCancel: () => void;
}

export function OAuthConfirmModal({
  open,
  providerId,
  providerName,
  onSuccess,
  onCancel,
}: OAuthConfirmModalProps) {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const [phase, setPhase] = useState<"confirm" | "waiting" | "failed">(
    "confirm",
  );
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeRef = useRef(false);
  const runRef = useRef(0);

  const stop = useCallback(() => {
    runRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    if (pollRef.current) clearTimeout(pollRef.current);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    pollRef.current = null;
    timeoutRef.current = null;
  }, []);

  useEffect(() => {
    activeRef.current = open;
    if (open) setPhase("confirm");
    else stop();
    return () => {
      activeRef.current = false;
      stop();
    };
  }, [open, stop]);

  const handleCancel = useCallback(() => {
    activeRef.current = false;
    stop();
    onCancel();
  }, [onCancel, stop]);

  const handleContinue = useCallback(async () => {
    stop();
    activeRef.current = true;
    const run = runRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const { authorize_url, state } = await providerApi.startOAuth(
        providerId,
        controller.signal,
      );
      if (!activeRef.current || run !== runRef.current) return;
      setPhase("waiting");

      openExternalLink(authorize_url, "_blank", "popup,width=600,height=700");

      // Poll backend status until completion (same pattern as MCP OAuth)
      const poll = async () => {
        if (!activeRef.current || run !== runRef.current) return;
        try {
          const { status } = await providerApi.getOAuthStatus(
            providerId,
            state,
            controller.signal,
          );
          if (!activeRef.current || run !== runRef.current) return;
          if (status === "completed") {
            stop();
            message.success(
              t("modelSelector.oauthConnected", { provider: providerName }),
            );
            onSuccess();
          } else if (status === "failed") {
            if (pollRef.current) clearTimeout(pollRef.current);
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
            pollRef.current = null;
            timeoutRef.current = null;
            setPhase("failed");
            message.error(t("modelSelector.oauthFailed"));
          } else {
            pollRef.current = setTimeout(() => void poll(), 2000);
          }
        } catch (error) {
          if (
            activeRef.current &&
            run === runRef.current &&
            !(error instanceof DOMException && error.name === "AbortError")
          ) {
            pollRef.current = setTimeout(() => void poll(), 2000);
          }
        }
      };
      pollRef.current = setTimeout(() => void poll(), 2000);

      // Timeout after 5 minutes
      timeoutRef.current = setTimeout(() => {
        if (!activeRef.current || run !== runRef.current) return;
        if (pollRef.current) clearTimeout(pollRef.current);
        pollRef.current = null;
        setPhase("failed");
      }, 300000);
    } catch (err) {
      if (
        !activeRef.current ||
        run !== runRef.current ||
        (err instanceof DOMException && err.name === "AbortError")
      ) {
        return;
      }
      setPhase("failed");
      message.error(
        err instanceof Error ? err.message : t("modelSelector.oauthFailed"),
      );
    }
  }, [providerId, providerName, onSuccess, message, stop, t]);

  return (
    <Modal
      open={open}
      onCancel={handleCancel}
      footer={null}
      closable={phase !== "waiting"}
      maskClosable={phase !== "waiting"}
      width={420}
    >
      {phase === "confirm" ? (
        <div style={{ textAlign: "center", padding: "16px 0" }}>
          <ExternalLink
            size={40}
            style={{ color: "#6366f1", marginBottom: 16 }}
          />
          <h3 style={{ margin: "0 0 8px", fontSize: 16, fontWeight: 600 }}>
            {t("modelSelector.oauthTitle", { provider: providerName })}
          </h3>
          <p
            style={{
              color: "var(--text-secondary, rgba(0,0,0,0.45))",
              margin: "0 0 24px",
            }}
          >
            {t("modelSelector.oauthDescription", { provider: providerName })}
          </p>
          <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
            <Button onClick={handleCancel}>{t("common.cancel")}</Button>
            <Button type="primary" onClick={handleContinue}>
              {t("modelSelector.oauthContinue")}
            </Button>
          </div>
        </div>
      ) : phase === "waiting" ? (
        <div style={{ textAlign: "center", padding: "24px 0" }}>
          <Loader2
            size={32}
            style={{ color: "#6366f1", animation: "spin 1s linear infinite" }}
          />
          <h3 style={{ margin: "16px 0 8px", fontSize: 16, fontWeight: 600 }}>
            {t("modelSelector.oauthWaiting")}
          </h3>
          <p
            style={{
              color: "var(--text-secondary, rgba(0,0,0,0.45))",
              margin: "0 0 24px",
            }}
          >
            {t("modelSelector.oauthWaitingDescription")}
          </p>
          <Button onClick={handleCancel}>{t("common.cancel")}</Button>
        </div>
      ) : (
        <div style={{ textAlign: "center", padding: "24px 0" }}>
          <CircleX size={32} style={{ color: "#ef4444" }} />
          <h3 style={{ margin: "16px 0 8px", fontSize: 16, fontWeight: 600 }}>
            {t("modelSelector.oauthFailed")}
          </h3>
          <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
            <Button onClick={handleCancel}>{t("common.cancel")}</Button>
            <Button type="primary" onClick={() => void handleContinue()}>
              {t("common.retry", { defaultValue: "Retry" })}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
