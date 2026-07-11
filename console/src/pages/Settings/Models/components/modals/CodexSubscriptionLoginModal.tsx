import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Input, Modal } from "@agentscope-ai/design";
import { Check, CircleX, ExternalLink, Loader2 } from "lucide-react";
import { codexSubscriptionApi } from "../../../../../api/modules/codexSubscription";
import { openExternalLink } from "../../../../../utils/openExternalLink";
import { useAppMessage } from "../../../../../hooks/useAppMessage";

interface Props {
  open: boolean;
  flow?: "browser";
  onConnected: () => void;
  onClose: () => void;
}

export function CodexSubscriptionLoginModal({
  open,
  onConnected,
  onClose,
}: Props) {
  const { message } = useAppMessage();
  const [state, setState] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [phase, setPhase] = useState<
    "starting" | "waiting" | "connected" | "failed"
  >("starting");
  const [error, setError] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRef = useRef(false);
  const runRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const onConnectedRef = useRef(onConnected);
  const successRef = useRef(message.success);
  onConnectedRef.current = onConnected;
  successRef.current = message.success;

  const deactivate = useCallback(() => {
    activeRef.current = false;
    runRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const isCurrentRun = useCallback(
    (run: number) => activeRef.current && runRef.current === run,
    [],
  );

  const handleClose = useCallback(() => {
    deactivate();
    onClose();
  }, [deactivate, onClose]);

  useEffect(() => {
    if (!open) {
      deactivate();
      return;
    }

    activeRef.current = true;
    runRef.current += 1;
    const run = runRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setPhase("starting");
    setError("");
    setState("");
    setCallbackUrl("");

    const schedulePoll = (oauthState: string, delay: number) => {
      if (!isCurrentRun(run)) return;
      timer.current = setTimeout(async () => {
        if (!isCurrentRun(run)) return;
        try {
          const result = await codexSubscriptionApi.getLoginStatus(
            oauthState,
            controller.signal,
          );
          if (!isCurrentRun(run)) return;
          if (result.status === "completed") {
            timer.current = null;
            setPhase("connected");
            successRef.current("ChatGPT 登录成功");
            onConnectedRef.current();
            return;
          }
          if (result.status === "failed" || result.status === "expired") {
            timer.current = null;
            setPhase("failed");
            setError(
              result.error ||
                (result.status === "expired"
                  ? "登录请求已过期，请重新开始"
                  : "ChatGPT 登录失败"),
            );
            return;
          }
        } catch (reason) {
          if (!isCurrentRun(run)) return;
          if (reason instanceof DOMException && reason.name === "AbortError") {
            return;
          }
        }
        if (isCurrentRun(run)) schedulePoll(oauthState, 1500);
      }, delay);
    };

    void codexSubscriptionApi
      .startLogin(controller.signal)
      .then((login) => {
        if (!isCurrentRun(run)) return;
        setState(login.state);
        setPhase("waiting");
        openExternalLink(
          login.authorize_url,
          "_blank",
          "popup,width=640,height=760",
        );
        schedulePoll(login.state, 1000);
      })
      .catch((reason: unknown) => {
        if (!isCurrentRun(run)) return;
        if (reason instanceof DOMException && reason.name === "AbortError") {
          return;
        }
        setPhase("failed");
        setError(reason instanceof Error ? reason.message : "无法开始登录");
      });

    return deactivate;
  }, [deactivate, isCurrentRun, open]);

  const completeManual = async () => {
    if (!activeRef.current) return;
    runRef.current += 1;
    const run = runRef.current;
    abortRef.current?.abort();
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await codexSubscriptionApi.completeLogin(
        {
          callback_url: callbackUrl,
          state,
        },
        controller.signal,
      );
      if (!isCurrentRun(run)) return;
      setPhase("connected");
      message.success("ChatGPT 登录成功");
      onConnected();
    } catch (reason) {
      if (!isCurrentRun(run)) return;
      if (reason instanceof DOMException && reason.name === "AbortError") {
        return;
      }
      setPhase("failed");
      setError(reason instanceof Error ? reason.message : "无法完成登录");
    }
  };

  return (
    <Modal open={open} onCancel={handleClose} footer={null} width={520}>
      <div style={{ padding: 16, textAlign: "center" }}>
        {phase === "connected" ? (
          <Check size={36} color="#22c55e" />
        ) : phase === "failed" ? (
          <CircleX size={36} color="#ef4444" />
        ) : (
          <Loader2 size={34} style={{ animation: "spin 1s linear infinite" }} />
        )}
        <h3>登录 ChatGPT</h3>
        <p>
          浏览器授权后会自动返回 QwenPaw。若浏览器与 QwenPaw
          不在同一台机器，localhost 页面无法打开属于正常情况。
        </p>
        {phase !== "connected" && (
          <>
            <p>请复制浏览器地址栏中的完整 callback URL 并粘贴到此处：</p>
            <Input
              value={callbackUrl}
              onChange={(event) => setCallbackUrl(event.target.value)}
              placeholder="http://localhost:1455/auth/callback?code=...&state=..."
            />
            <Button
              type="primary"
              disabled={!callbackUrl.trim()}
              onClick={completeManual}
              style={{ marginTop: 12 }}
            >
              完成登录
            </Button>
            <Button
              icon={<ExternalLink size={15} />}
              onClick={handleClose}
              style={{ marginTop: 12, marginLeft: 8 }}
            >
              取消
            </Button>
          </>
        )}
        {error && <p style={{ color: "#ef4444" }}>{error}</p>}
      </div>
    </Modal>
  );
}
