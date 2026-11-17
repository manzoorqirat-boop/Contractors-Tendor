import { defineConfig } from 'vitest/config';

// Unit tests of the page's plain logic (formatting, filters). They run in Node with no browser: anything that needs a screen is covered by `npm run a11y`.
export default defineConfig({
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
