import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

/**
 * CDP 客户端工具：基于 D:\Users\Administrator\Desktop\插件 项目的 DevToolsActivePort 原生设计
 */
export function getDevToolsActivePort() {
  const localAppData = process.env.LOCALAPPDATA;
  if (!localAppData) throw new Error('LOCALAPPDATA not found');
  
  const candidatePaths = [
    join(localAppData, 'Google/Chrome/User Data/DevToolsActivePort'),
    join(localAppData, 'Microsoft/Edge/User Data/DevToolsActivePort'),
    join(localAppData, 'Chromium/User Data/DevToolsActivePort'),
  ];

  for (const p of candidatePaths) {
    if (existsSync(p)) {
      const lines = readFileSync(p, 'utf8').trim().split(/\r?\n/).map(l => l.trim()).filter(Boolean);
      const port = Number(lines[0]);
      const browserPath = lines[1] && lines[1].startsWith('/') ? lines[1] : '/devtools/browser';
      if (port > 0) return { port, browserPath, filePath: p };
    }
  }
  return null;
}

export async function createCdpClient() {
  const portInfo = getDevToolsActivePort();
  if (!portInfo) throw new Error('未找到运行中的浏览器 DevToolsActivePort 文件，请确认 Chrome 是否已开启 --remote-debugging-port');

  const wsUrl = `ws://127.0.0.1:${portInfo.port}${portInfo.browserPath}`;
  const ws = new WebSocket(wsUrl);

  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = (e) => reject(new Error('WebSocket 连接失败: ' + e.message));
  });

  let messageId = 1;
  const pending = new Map();

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) {
        const { resolve, reject } = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message || JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    } catch (e) {
      console.error('CDP message parse error:', e);
    }
  };

  function send(method, params = {}, sessionId = undefined) {
    const id = messageId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      const payload = { id, method, params };
      if (sessionId) payload.sessionId = sessionId;
      ws.send(JSON.stringify(payload));
    });
  }

  async function getTargets() {
    const res = await send('Target.getTargets');
    return res.targetInfos || [];
  }

  async function attachToPage(predicate) {
    const targets = await getTargets();
    const target = targets.find(t => t.type === 'page' && predicate(t));
    if (!target) throw new Error('未找到匹配的目标页面');
    const attached = await send('Target.attachToTarget', { targetId: target.targetId, flatten: true });
    return {
      target,
      sessionId: attached.sessionId,
      send: (method, params = {}) => send(method, params, attached.sessionId),
      eval: async (expr) => {
        const res = await send('Runtime.evaluate', { expression: expr, returnByValue: true }, attached.sessionId);
        return res?.result?.value;
      },
      screenshot: async () => {
        const res = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, attached.sessionId);
        return Buffer.from(res.data, 'base64');
      },
      reload: async () => {
        await send('Page.reload', { ignoreCache: true }, attached.sessionId);
      }
    };
  }

  return {
    ws,
    portInfo,
    send,
    getTargets,
    attachToPage,
    close: () => ws.close()
  };
}

// CLI 执行模式
if (process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('cdp.mjs')) {
  const action = process.argv[2] || 'status';
  (async () => {
    try {
      const client = await createCdpClient();
      console.log(`✅ 已连接 Chrome CDP (Port ${client.portInfo.port})`);
      if (action === 'status' || action === 'list') {
        const targets = await client.getTargets();
        console.log(`\n📋 当前所有页面/Tab (${targets.filter(t => t.type === 'page').length} 个标签页):`);
        targets.filter(t => t.type === 'page').forEach((t, i) => {
          console.log(`  [${i + 1}] ${t.title || '（无标题）'} -> ${t.url}`);
        });
      } else if (action === 'reload') {
        const page = await client.attachToPage(t => t.url.includes('8089') || t.url.includes('合同'));
        await page.reload();
        console.log(`🔄 页面已硬重载: ${page.target.url}`);
      } else if (action === 'shot') {
        const page = await client.attachToPage(t => t.url.includes('8089') || t.url.includes('合同'));
        const outPath = process.argv[3] || 'C:/Users/Administrator/.gemini/antigravity/brain/3b89fd95-fea2-488d-85e0-1e211d614f78/cdp_shot.png';
        const buf = await page.screenshot();
        const { writeFileSync } = await import('node:fs');
        writeFileSync(outPath, buf);
        console.log(`📸 截图已保存至: ${outPath}`);
      }
      client.close();
      process.exit(0);
    } catch (err) {
      console.error('CDP 操作失败:', err.message);
      process.exit(1);
    }
  })();
}
