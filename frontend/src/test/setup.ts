import { configure } from "@testing-library/react";

// Parallel CI workers can spend several seconds transforming large product
// surfaces before React commits an async result. Keep queries aligned with the
// test runner's existing 15-second contract instead of relying on the library's
// one-second local-machine default.
configure({ asyncUtilTimeout: 10_000 });
