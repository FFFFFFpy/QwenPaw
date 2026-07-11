import React from "react";
import type { ProviderInfo } from "../../../../../api/types";
import { CodexSubscriptionPanel } from "./CodexSubscriptionPanel";

interface Props {
  provider: ProviderInfo;
  onSaved: () => void;
  onOpenModels: (provider: ProviderInfo) => void;
}

export const SubscriptionProviderCard = React.memo(
  function SubscriptionProviderCard(props: Props) {
    return <CodexSubscriptionPanel {...props} variant="card" />;
  },
);
