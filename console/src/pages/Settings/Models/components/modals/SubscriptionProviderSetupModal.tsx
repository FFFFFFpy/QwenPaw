import { Modal } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import type { ProviderInfo } from "../../../../../api/types";
import { CodexSubscriptionPanel } from "../cards/CodexSubscriptionPanel";

interface Props {
  provider: ProviderInfo;
  open: boolean;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
  onOpenModels: (provider: ProviderInfo) => void;
}

export function SubscriptionProviderSetupModal({
  provider,
  open,
  onClose,
  onSaved,
  onOpenModels,
}: Props) {
  const { t } = useTranslation();

  const complete = async () => {
    await onSaved();
    onClose();
  };

  return (
    <Modal
      open={open}
      title={t("models.codexSubscription.setupTitle", {
        name: provider.name,
      })}
      footer={null}
      onCancel={onClose}
      destroyOnClose
      width={680}
    >
      <CodexSubscriptionPanel
        provider={provider}
        variant="setup"
        onSaved={onSaved}
        onOpenModels={onOpenModels}
        onComplete={() => void complete()}
      />
    </Modal>
  );
}
