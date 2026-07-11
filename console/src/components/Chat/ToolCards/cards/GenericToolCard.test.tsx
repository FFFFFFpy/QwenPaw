import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GenericToolCard from "./GenericToolCard";

describe("GenericToolCard", () => {
  it("renders an image returned by image_generate", () => {
    const { container } = render(
      <GenericToolCard
        content={{
          type: "tool_call",
          id: "call-image",
          name: "image_generate",
          params: {},
          status: "done",
          result: [
            {
              type: "data",
              source: {
                type: "url",
                url: "file:///workspace/resources/generated.png",
                media_type: "image/png",
              },
              name: "generated.png",
            },
            {
              type: "data",
              source: {
                type: "url",
                url: "file:///workspace/resources/generated-2.png",
                media_type: "image/png",
              },
              name: "generated-2.png",
            },
          ],
        }}
      />,
    );

    const images = container.querySelectorAll("img.ant-image-img");
    const details = container.querySelector("details");
    expect(details).toHaveAttribute("open");
    expect(
      container.querySelector("div[class*='toolCallMediaGrid']"),
    ).not.toBeNull();
    expect(images).toHaveLength(2);
    expect(images[0]).toHaveAttribute(
      "src",
      expect.stringContaining(
        "/files/preview/workspace/resources/generated.png",
      ),
    );
    expect(container).not.toHaveTextContent("Output");
  });
});
