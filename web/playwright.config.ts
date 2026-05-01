import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',

  // 超时设置
  timeout: 60 * 1000,
  expect: {
    timeout: 10 * 1000,
  },

  // 失败重试
  retries: 0, // 禁用重试以便更快完成测试

  // 并行执行
  workers: 1,

  // 报告配置
  reporter: [
    ['list'],
    ['html', { open: 'never' }],
  ],

  use: {
    // 基础 URL
    baseURL: 'http://localhost:9119',

    // 浏览器选项 - 使用 headless 模式
    headless: true,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'retain-on-failure',

    // 浏览器启动选项
    launchOptions: {
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-software-rasterizer',
      ],
    },
  },

  // 项目配置
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // 使用已安装的 Chromium (版本 1208) - 包括 headless shell
        executablePath: '/home/liu/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome',
      },
    },
    {
      name: 'chromium-headless',
      use: {
        ...devices['Desktop Chrome'],
        // 使用已安装的 headless shell (版本 1208)
        executablePath: '/home/liu/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell',
      },
    },
  ],
});
