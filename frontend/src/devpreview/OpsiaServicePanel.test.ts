import { Braces, KeyRound, Layers3, Network, Plug, ShoppingCart } from "lucide-react";
import { describe, expect, it } from "vitest";

import { serviceIconStyle } from "./OpsiaServicePanel";

describe("service semantic icon rules", () => {
  it("classifies roles from generic keywords instead of exact service names", () => {
    expect(serviceIconStyle("catalog-api-v2").Icon).toBe(Braces);
    expect(serviceIconStyle("edge-gateway-prod").Icon).toBe(Network);
    expect(serviceIconStyle("orders-checkout").Icon).toBe(ShoppingCart);
    expect(serviceIconStyle("session-redis-primary").Icon).toBe(Layers3);
    expect(serviceIconStyle("oauth-identity").Icon).toBe(KeyRound);
  });

  it("keeps an unknown Kubernetes Service neutral", () => {
    expect(serviceIconStyle("canary-room").Icon).toBe(Plug);
  });
});
