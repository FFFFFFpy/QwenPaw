/**
 * GenericToolCard — fallback card for tool calls not in the builtin registry.
 *
 * Shows the tool name + spinner while no output is available,
 * then a collapsible result block once the tool completes.
 */

import React from "react";
import { useTranslation } from "react-i18next";
import { ToolOutlined } from "@ant-design/icons";
import type { ToolCallContent } from "../shared/types";
import { MediaPreview, ToolCardShell } from "../shared";
import { DefaultBlock } from "../shared";
import { getMediaInfos, stringifyResult } from "../shared/utils";
import styles from "../shared/toolCards.module.less";

export interface GenericToolCardProps {
  content: ToolCallContent;
  isStreaming?: boolean;
}

const GenericToolCard: React.FC<GenericToolCardProps> = ({
  content,
  isStreaming,
}) => {
  const { t } = useTranslation();
  const toolLabel = content.serverLabel
    ? `${content.serverLabel} / ${content.name}`
    : content.name;
  const resultText = stringifyResult(content.result);
  const media = getMediaInfos(content);
  const hasMedia = media.length > 0;

  return (
    <ToolCardShell
      icon={<ToolOutlined />}
      title={t("tool.execute", { tool: toolLabel })}
      content={content}
      isStreaming={isStreaming}
      defaultOpen={hasMedia && content.status === "done"}
    >
      {hasMedia && (
        <div
          className={media.length > 1 ? styles.toolCallMediaGrid : undefined}
        >
          {media.map((item) => (
            <MediaPreview media={item} key={`${item.url}-${item.name}`} />
          ))}
        </div>
      )}
      {!hasMedia && resultText && (
        <DefaultBlock title="Output" content={resultText} />
      )}
    </ToolCardShell>
  );
};

export default GenericToolCard;
