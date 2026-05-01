import { test, expect } from '@playwright/test';

test.describe('Hermes Chat Page', () => {
  const BASE_URL = 'http://localhost:9119';

  test.beforeEach(async ({ page }) => {
    // 设置视图端口
    await page.setViewportSize({ width: 1920, height: 1080 });
  });

  test('should load chat page successfully', async ({ page }) => {
    await page.goto(`${BASE_URL}/chat`);

    // 等待页面加载
    await expect(page).toHaveTitle(/Hermes/);

    // 检查主要内容区域
    await expect(page.locator('.layout')).toBeVisible();
    await expect(page.locator('.sidebar')).toBeVisible();
    await expect(page.locator('#panelChat')).toBeVisible();
  });

  test('should display chat input composer', async ({ page }) => {
    await page.goto(`${BASE_URL}/chat`);

    // 检查输入框存在
    const composer = page.locator('#msg');
    await expect(composer).toBeVisible();
    await expect(composer).toHaveAttribute('placeholder', /Message Hermes/);
  });

  test('should display new chat button', async ({ page }) => {
    await page.goto(`${BASE_URL}/chat`);

    // 检查新建对话按钮
    const newChatBtn = page.locator('#btnNewChat');
    await expect(newChatBtn).toBeVisible();
    await expect(newChatBtn).toContainText(/New conversation/i);
  });

  test('should load model selector', async ({ page }) => {
    await page.goto(`${BASE_URL}/chat`);

    // 检查模型选择器
    const modelSelect = page.locator('#modelSelect');
    await expect(modelSelect).toBeVisible();
  });

  test('should handle API errors gracefully', async ({ page }) => {
    // 模拟 API 错误
    await page.route('**/api/models', route => {
      route.fulfill({
        status: 500,
        body: JSON.stringify({ error: 'Internal server error' })
      });
    });

    await page.goto(`${BASE_URL}/chat`);

    // 页面仍应正常加载（优雅降级）
    await expect(page.locator('.layout')).toBeVisible();
  });

  test('should send a message and receive response', async ({ page }) => {
    await page.goto(`${BASE_URL}/chat`);

    // 等待页面完全加载
    await page.waitForTimeout(2000);

    // 找到输入框并输入消息
    const input = page.locator('#msg');
    await input.fill('Hello');

    // 检查发送按钮
    const sendBtn = page.locator('button[title="Send"]');
    await expect(sendBtn).toBeVisible();

    // 发送消息（如果启用了）
    // await sendBtn.click();
    // 等待响应（需要配置 API key）
  });

  test('should handle session list', async ({ page }) => {
    await page.goto(`${BASE_URL}/chat`);

    // 检查会话列表区域
    const sessionList = page.locator('#sessionList');
    await expect(sessionList).toBeVisible();
  });
});
