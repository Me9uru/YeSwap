import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
export default defineConfig({
  plugins: [vue()],
  test: {
    server: {
      deps: { inline: [/element-plus/] },
    },
    environment: "jsdom",
    include: ["tests/**/*.spec.js"],
    setupFiles: ["tests/setup.js"],
    restoreMocks: true,
  },
});
