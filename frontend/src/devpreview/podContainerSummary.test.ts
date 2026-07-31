import { describe, expect, it } from "vitest";

import { podContainerSummary } from "./podContainerSummary";

describe("podContainerSummary", () => {
  it("extracts container images and declared ports from inventory summary", () => {
    expect(podContainerSummary({
      container_ports_complete: true,
      containers: [
        {
          name: "api",
          image: "registry.example.com/api:v1",
          ports: [
            { container_port: 8080, name: "http", protocol: "TCP" },
            { container_port: 8443, name: "https", protocol: "TCP" },
          ],
        },
        {
          name: "metrics",
          image: "registry.example.com/metrics:v2",
          ports: [{ container_port: 9090, protocol: "tcp" }],
        },
      ],
    })).toEqual({
      containerPortsComplete: true,
      containers: [
        {
          name: "api",
          image: "registry.example.com/api:v1",
          ports: [
            { port: 8080, name: "http", protocol: "TCP" },
            { port: 8443, name: "https", protocol: "TCP" },
          ],
        },
        {
          name: "metrics",
          image: "registry.example.com/metrics:v2",
          ports: [{ port: 9090, name: null, protocol: "TCP" }],
        },
      ],
    });
  });

  it("preserves explicit no-port observations separately from unknown completeness", () => {
    expect(podContainerSummary({
      container_ports_complete: true,
      containers: [{ name: "worker", image: "worker:v1", ports: [] }],
    })).toMatchObject({
      containerPortsComplete: true,
      containers: [{ name: "worker", ports: [] }],
    });

    expect(podContainerSummary({
      containers: [{ name: "worker", image: "worker:v1", ports: [] }],
    }).containerPortsComplete).toBeNull();
  });

  it("ignores malformed containers and ports without fabricating values", () => {
    expect(podContainerSummary({
      container_ports_complete: "yes",
      containers: [
        null,
        { name: "", image: "ignored:v1" },
        {
          name: "api",
          image: "",
          ports: [
            { container_port: 0, protocol: "TCP" },
            { container_port: 80, protocol: "FTP" },
            { container_port: 81, protocol: 123 },
            { container_port: 82, protocol: "" },
            { container_port: 53, protocol: "UDP" },
            { container_port: 53, protocol: "UDP" },
          ],
        },
        { name: "api", image: "duplicate:v1" },
      ],
    })).toEqual({
      containerPortsComplete: null,
      containers: [{
        name: "api",
        image: null,
        ports: [{ port: 53, name: null, protocol: "UDP" }],
      }],
    });
  });
});
