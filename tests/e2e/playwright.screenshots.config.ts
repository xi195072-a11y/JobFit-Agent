import baseConfig from "./playwright.config";

/**
 * 截图专用配置（§36）：与默认 E2E 分离，不进入 `npx playwright test`。
 * 用法：npm run screenshots
 */
export default {
  ...baseConfig,
  testDir: "./screenshots",
  reporter: [["list"]],
};
