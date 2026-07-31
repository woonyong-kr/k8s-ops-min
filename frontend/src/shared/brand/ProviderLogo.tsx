import { Boxes, Server } from "lucide-react";
import { useState } from "react";

import awsDarkLogo from "./aws-dark.png";
import awsLogo from "./aws.png";
import azureLogo from "./azure.svg";
import gcpLogo from "./gcp.png";

export type ProviderLogoKind = "aks" | "eks" | "gke" | "kind" | "onprem" | "unknown";

export function ProviderLogo({
  className,
  provider,
}: {
  className?: string;
  provider: ProviderLogoKind;
}) {
  // 이미지 에셋(aws/azure/gcp)이 로드에 실패해도 아이콘이 사라지지 않도록
  // lucide 아이콘으로 폴백한다. onprem·kind·unknown 은 항상 벡터 아이콘을 쓴다.
  const [imageFailed, setImageFailed] = useState(false);

  if (!imageFailed && provider === "eks") {
    return (
      <span aria-hidden="true" className={className}>
        <img
          alt=""
          className="size-full object-contain dark:hidden"
          onError={() => setImageFailed(true)}
          src={awsLogo}
        />
        <img
          alt=""
          className="hidden size-full object-contain dark:block"
          onError={() => setImageFailed(true)}
          src={awsDarkLogo}
        />
      </span>
    );
  }
  if (!imageFailed && (provider === "aks" || provider === "gke")) {
    return (
      <img
        alt=""
        aria-hidden="true"
        className={className}
        onError={() => setImageFailed(true)}
        src={provider === "aks" ? azureLogo : gcpLogo}
      />
    );
  }
  const Icon = provider === "kind" ? Boxes : Server;
  return <Icon aria-hidden="true" className={className} />;
}
