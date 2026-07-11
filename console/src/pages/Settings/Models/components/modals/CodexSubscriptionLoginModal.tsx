import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Input, Modal } from "@agentscope-ai/design";
import { Check, ExternalLink, Loader2 } from "lucide-react";
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

  const poll = useCallback(
    (oauthState: string) => {
      const run = async () => {
        try {
          const result = await codexSubscriptionApi.getLoginStatus(oauthState);
          if (result.status === "completed") {
            setPhase("connected");
            message.success("ChatGPT 登录成功");
            onConnected();
            return;
          }
        } catch {
          /* keep the active OAuth session */
        }
        timer.current = setTimeout(run, 1500);
      };
      timer.current = setTimeout(run, 1000);
    },
    [message, onConnected],
  );

  useEffect(() => {
    if (!open) return;
    setPhase("starting");
    setError("");
    setCallbackUrl("");
    void codexSubscriptionApi
      .startLogin()
      .then((login) => {
        setState(login.state);
        setPhase("waiting");
        openExternalLink(
          login.authorize_url,
          "_blank",
          "popup,width=640,height=760",
        );
        poll(login.state);
      })
      .catch((reason: unknown) => {
        setPhase("failed");
        setError(reason instanceof Error ? reason.message : "无法开始登录");
      });
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [open, poll]);

  const completeManual = async () => {
    try {
      await codexSubscriptionApi.completeLogin({
        callback_url: callbackUrl,
        state,
      });
      setPhase("connected");
      message.success("ChatGPT 登录成功");
      onConnected();
    } catch (reason) {
      setPhase("failed");
      setError(reason instanceof Error ? reason.message : "无法完成登录");
    }
  };

  return (
    <Modal open={open} onCancel={onClose} footer={null} width={520}>
      <div style={{ padding: 16, textAlign: "center" }}>
        {phase === "connected" ? (
          <Check size={36} color="#22c55e" />
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
              onClick={onClose}
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
