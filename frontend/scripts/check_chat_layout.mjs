// UI verification with local API fixtures; no real login, DB or provider calls.
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const dist = path.join(repo, 'frontend/dist');
const output = path.join(repo, 'backend/storage/tmp/chat-layout-check');
const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
await fs.access(edge);
await fs.mkdir(output, { recursive: true });
const annotation = ' (tham khảo mục PAGE_1_SECTION_3 Điều 114: ' + 'Nội dung tham khảo dài để kiểm tra bố cục. '.repeat(8) + ', trang 1)';
const suggestions = Array.from({ length: 12 }, (_, index) => ({ suggestion_id: `test-${index}`, catalog_version: 'v1', prompt: `Xem số ngày phép hoặc chấm công ${index}${annotation}` }));
const sources = [1, 2].map(id => ({ document_id: 1, version_id: 2, section_id: id, section_code: `PAGE_1_SECTION_${id}`, title: 'Quy chế nghỉ phép năm', heading: id === 1 ? 'Định mức nghỉ phép năm' : 'Điều kiện được tăng ngày nghỉ theo thâm niên. '.repeat(8), page_start: 1, page_end: 1, excerpt: 'Nguồn fixture', viewer_path: `/knowledge/view/1?version=2&section=${id}` }));
const requests = [];
const server = http.createServer(async (req, res) => {
  const json = value => { res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(value)); };
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/api/v1/auth/me') return json({ user_account_id: 9, employee_id: 7, login_email: 'fixture@example.test', roles: ['HR'], is_active: true });
    if (url.pathname === '/api/v1/ai/suggestions') return json(suggestions);
    if (url.pathname === '/api/v1/ai/chat') {
      let body = ''; for await (const chunk of req) body += chunk;
      const request = JSON.parse(body); requests.push(request);
      return json(request.message === 'public test'
        ? { reply: 'Thông tin tham khảo công khai [1].', sources: [], external_sources: [{ title: 'Nguồn nhà nước minh họa', url: 'https://vanban.chinhphu.vn/fixture' }], search_suggestions_html: '<p>Google Search fixture</p><script>parent.__unsafeSearchHTML=true</script>', action: null, status: 'OK' }
        : { reply: 'Số dư phép năm của bạn: 12.00 ngày.', sources, external_sources: [], action: { action_type: 'SHOW_DATA', data: { type: 'LEAVE_BALANCE', balance: '12.00' } }, status: 'OK' });
    }
    if (url.pathname.startsWith('/api/')) return json([]);
    if (url.pathname.startsWith('/assets/')) {
      const filename = path.resolve(dist, '.' + url.pathname);
      if (!filename.startsWith(dist + path.sep)) return res.writeHead(404).end();
      res.writeHead(200, { 'Content-Type': ({ '.js': 'text/javascript', '.css': 'text/css' })[path.extname(filename)] || 'application/octet-stream' });
      return res.end(await fs.readFile(filename));
    }
    const html = await fs.readFile(path.join(dist, 'index.html'), 'utf8');
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(html.replace('</head>', '<script>localStorage.setItem("access_token","local-chat-fixture")</script></head>'));
  } catch { res.writeHead(500).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
try {
  for (const [name, width, height] of [['desktop', 1280, 900], ['compact', 768, 900], ['mobile', 390, 844]]) {
    const profile = path.join(output, `profile-${name}-${Date.now()}`);
    const browser = spawn(edge, ['--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: ['ignore', 'ignore', 'ignore'] });
    const timeout = setTimeout(() => browser.kill(), 30000);
    let socket;
    try {
      let debugPort;
      for (let attempt = 0; attempt < 100; attempt++) {
        try { debugPort = Number((await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); break; } catch { await pause(50); }
      }
      if (!debugPort) throw Error('Edge debugging endpoint did not start');
      const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
      socket = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
      await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
      let sequence = 0;
      const pending = new Map();
      socket.addEventListener('message', event => {
        const value = JSON.parse(event.data); const handler = pending.get(value.id);
        if (handler) { pending.delete(value.id); value.error ? handler.reject(Error(value.error.message)) : handler.resolve(value.result); }
      });
      const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
      const evaluate = async expression => { const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (result.exceptionDetails) throw Error(result.exceptionDetails.text); return result.result.value; };
      const until = async expression => { for (let i = 0; i < 100; i++) { if (await evaluate(expression)) return; await pause(100); } throw Error(`UI timeout: ${expression}`); };
      const screenshot = async suffix => { const shot = await send('Page.captureScreenshot', { format: 'png' }); await fs.writeFile(path.join(output, `${name}-${suffix}.png`), Buffer.from(shot.data, 'base64')); };
      await send('Page.enable');
      await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
      await send('Page.navigate', { url: origin + '/knowledge' });
      await until('!!document.querySelector("button .anticon-robot")');
      await evaluate('document.querySelector("button .anticon-robot").closest("button").click()');
      await until('!!Array.from(document.querySelectorAll("button")).find(b=>b.textContent.includes("Gợi ý câu hỏi"))');
      await evaluate('Array.from(document.querySelectorAll("button")).find(b=>b.textContent.includes("Gợi ý câu hỏi")).click()');
      await until('!!document.querySelector(".ai-chat-suggestion-popup")');
      await pause(300);
      const menu = await evaluate(`(() => { const e=document.querySelector('.ai-chat-suggestion-popup'), r=e.getBoundingClientRect(), m=document.querySelector('.ant-modal').getBoundingClientRect(); return {left:r.left,right:r.right,width:r.width,modalLeft:m.left,modalRight:m.right,modalWidth:m.width,overflow:e.scrollWidth>e.clientWidth+1,raw:e.textContent.includes('PAGE_'),items:e.querySelectorAll('[role=menuitem]').length}; })()`);
      if (menu.left < 0 || menu.right > width + 1 || menu.left < menu.modalLeft || menu.right > menu.modalRight || menu.width > menu.modalWidth || menu.overflow || menu.raw || menu.items !== 12) throw Error('Invalid suggestion layout: ' + JSON.stringify(menu));
      await screenshot('suggestions');
      const before = requests.length;
      await evaluate('document.querySelector(".ai-chat-suggestion-popup [role=menuitem]").click()');
      await until('!!document.querySelector("details.ai-chat-source-group")');
      if (requests.length !== before + 1 || requests.at(-1).message !== suggestions[0].prompt || requests.at(-1).suggestion_id !== suggestions[0].suggestion_id) throw Error('Suggestion submission was modified');
      await evaluate('document.querySelector("details.ai-chat-source-group summary").click()');
      await pause(250);
      const refs = await evaluate(`(() => {const d=document.querySelector('details.ai-chat-source-group');return {groups:document.querySelectorAll('details.ai-chat-source-group').length,links:Array.from(d.querySelectorAll('a')).map(a=>a.getAttribute('href')),overflow:d.scrollWidth>d.clientWidth+1,dashed:document.querySelectorAll('.ant-modal [style*="dashed"]').length};})()`);
      if (refs.groups !== 1 || refs.links.join('|') !== sources.map(s=>s.viewer_path).join('|') || refs.overflow || refs.dashed) throw Error('Invalid source grouping: ' + JSON.stringify(refs));
      await screenshot('sources');
      await evaluate(`(()=>{const e=document.querySelector('.ant-modal textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,'public test');e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
      await pause(100);
      await evaluate('Array.from(document.querySelectorAll(".ant-modal button")).find(b=>b.textContent.trim()==="Gửi").click()');
      await until('!!document.querySelector(".ant-modal iframe[srcdoc]")');
      const external = await evaluate(`(()=>{const f=document.querySelector('.ant-modal iframe[srcdoc]');return {sandbox:f.getAttribute('sandbox'),unsafe:!!window.__unsafeSearchHTML,url:!!document.querySelector('a[href="https://vanban.chinhphu.vn/fixture"][target="_blank"]')};})()`);
      if (external.sandbox.includes('allow-scripts') || external.sandbox.includes('allow-same-origin') || external.unsafe || !external.url) throw Error('Unsafe public source rendering');
      await screenshot('public');
      console.log(`PASS ${name}: bounded suggestions, original request, grouped citations, no empty separator, sandboxed public sources`);
      socket.send(JSON.stringify({ id: ++sequence, method: 'Browser.close' }));
      await pause(100);
    } finally {
      clearTimeout(timeout); socket?.close(); if (browser.exitCode === null) browser.kill();
    }
  }
  console.log('Browser checks used local API fixtures; no DB/provider integration was exercised.');
} finally { server.close(); }
