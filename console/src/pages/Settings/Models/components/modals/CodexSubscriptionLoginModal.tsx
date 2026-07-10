import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Modal } from "@agentscope-ai/design";
import { Check, Copy, ExternalLink, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  CodexLoginFlow,
  CodexLoginStart,
} from "../../../../../api/types/codexSubscription";
import { codexSubscriptionApi } from "../../../../../api/modules/codexSubscription";
import { openExternalLink } from "../../../../../utils/openExternalLink";
import { useAppMessage } from "../../../../../hooks/useAppMessage";

interface Props {
  open: boolean;
  flow: CodexLoginFlow;
  onConnected: () => void;
  onClose: () => void;
}

type Phase = "starting" | "waiting" | "connected" | "failed";

export function CodexSubscriptionLoginModal({
  open,
  flow,
  onConnected,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const [phase, setPhase] = useState<Phase>("starting");
  const [login, setLogin] = useState<CodexLoginStart | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const activeRef = useRef(false);

  const clearTimers = useCallback(() => {
    if (pollRef.current) clearTimeout(pollRef.current);
    if (timerRef.current) clearInterval(timerRef.current);
    pollRef.current = null;
    timerRef.current = null;
  }, []);

  useEffect(() => {
    if (!open) {
      activeRef.current = false;
      clearTimers();
      setLogin(null);
      return;
    }

    activeRef.current = true;
    setPhase("starting");
    setError(null);
    void codexSubscriptionApi
      .startLogin(flow)
      .then((result) => {
        if (!activeRef.current) return;
        setLogin(result);
        setPhase("waiting");
        setSecondsLeft(
          Math.max(0, result.expires_at - Math.floor(Date.now() / 1000)),
        );
        const url = result.authorize_url || result.verification_url;
        if (url && flow === "browser") {
          openExternalLink(url, "_blank", "popup,width=640,height=760");
        }
      })
      .catch((reason: unknown) => {
        if (!activeRef.current) return;
        setPhase("failed");
        setError(
          reason instanceof Error
            ? reason.message
            : t("models.codexSubscription.loginFailed"),
        );
      });

    return () => {
      activeRef.current = false;
      clearTimers();
    };
  }, [open, flow, clearTimers, t]);

  useEffect(() => {
    if (!open || phase !== "waiting" || !login) return;
    timerRef.current = setInterval(() => {
      setSecondsLeft(
        Math.max(0, login.expires_at - Math.floor(Date.now() / 1000)),
      );
    }, 1000);

    const poll = async () => {
      try {
        const status = await codexSubscriptionApi.getLoginStatus(login.state);
        if (!activeRef.current) return;
        if (status.status === "completed") {
          clearTimers();
          setPhase("connected");
          message.success(t("models.codexSubscription.connected"));
          onConnected();
          return;
        }
        if (["failed", "expired", "cancelled"].includes(status.status)) {
          clearTimers();
          setPhase("failed");
          setError(status.error || t("models.codexSubscription.loginFailed"));
          return;
        }
      } catch {
        // A transient poll failure must not discard an active login session.
      }
      if (activeRef.current) pollRef.current = setTimeout(poll, 2000);
    };
    pollRef.current = setTimeout(poll, 1000);
    return clearTimers;
  }, [open, phase, login, clearTimers, message, onConnected, t]);

  const cancel = useCallback(async () => {
    activeRef.current = false;
    clearTimers();
    if (login && phase === "waiting") {
      try {
        await codexSubscriptionApi.cancelLogin(login.state);
      } catch {
        // Closing the modal is still safe if the cancellation RPC races expiry.
      }
    }
    onClose();
  }, [clearTimers, login, onClose, phase]);

  const copyCode = async () => {
    if (!login?.user_code) return;
    await navigator.clipboard.writeText(login.user_code);
    message.success(t("models.codexSubscription.codeCopied"));
  };

  const verificationUrl = login?.verification_url;

  return (
    <Modal open={open} onCancel={cancel} footer={null} width={440}>
      <div style={{ textAlign: "center", padding: "20px 8px 8px" }}>
        {phase === "connected" ? (
          <Check size={38} color="#22c55e" />
        ) : (
          <Loader2
            size={34}
            style={{ color: "#6366f1", animation: "spin 1s linear infinite" }}
          />
        )}
        <h3 style={{ margin: "14px 0 8px" }}>
          {t(
            phase === "starting"
              ? "models.codexSubscription.startingLogin"
              : phase === "failed"
              ? "models.codexSubscription.loginFailed"
              : "models.codexSubscription.waitingLogin",
          )}
        </h3>

        {flow === "device_code" && phase === "waiting" && login && (
          <>
            <p style={{ color: "var(--text-secondary)" }}>
              {t("models.codexSubscription.deviceInstructions")}
            </p>
            <div
              style={{
                fontSize: 26,
                fontWeight: 700,
                letterSpacing: 3,
                margin: "16px 0",
              }}
            >
              {login.user_code}
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              <Button icon={<Copy size={15} />} onClick={copyCode}>
                {t("models.codexSubscription.copyCode")}
              </Button>
              <Button
                type="primary"
                icon={<ExternalLink size={15} />}
                onClick={() =>
                  verificationUrl && openExternalLink(verificationUrl, "_blank")
                }
              >
                {t("models.codexSubscription.openBrowser")}
              </Button>
            </div>
            <p style={{ color: "var(--text-secondary)", marginTop: 14 }}>
              {t("models.codexSubscription.expiresIn", {
                seconds: secondsLeft,
              })}
            </p>
          </>
        )}

        {flow === "browser" && phase === "waiting" && (
          <p style={{ color: "var(--text-secondary)" }}>
            {t("models.codexSubscription.browserInstructions")}
          </p>
        )}
        {error && <p style={{ color: "#ef4444" }}>{error}</p>}
        <Button style={{ marginTop: 18 }} onClick={cancel}>
          {t("common.cancel")}
        </Button>
      </div>
    </Modal>
  );
}
