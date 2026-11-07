# -*- coding: utf-8 -*-
"""tender_sync_web.py — 全量同步实时进度 Web（零第三方依赖，stdlib http.server）。

- `/`          实时进度页（卡哇伊风）。数据由服务端直接渲染进页面（window.__STATUS__），
               不依赖 fetch 也能显示；fetch 可用时 2s 局部刷新，
               fetch 被沙箱/代理拦截时自动降级为整页刷新，无 JS 时 meta refresh 兜底。
- `/api/status` 进度 JSON（直接透传 progress.json 快照）

用法
  独立运行: python tools/tender_sync_web.py [--port 8770] [--progress data/tender_sync/progress.json]
  内嵌运行: tender_fullsync.py 自动 import 本模块并后台启动（start_in_thread）
"""

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PROGRESS = os.path.join(ROOT, "data", "tender_sync", "progress.json")
BG_PATH = os.path.join(ROOT, "tools", "assets", "sync-background.png")

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<noscript><meta http-equiv="refresh" content="5"></noscript>
<title>tender 全量同步 · 实时进度</title>
<style>
  :root { --pink:#FF9EC4; --pink-deep:#E26A9B; --pink-soft:#FFEAF2; --mint:#7EDDBB;
          --mint-deep:#2FA37E; --mint-soft:#E7F8F1; --lav:#C9BDF6; --lav-soft:#F1EDFF;
          --blue:#8FC7F5; --blue-soft:#E7F3FF; --peach:#FFC9A3; --peach-soft:#FFF0E4;
          --ink:#5B4A5E; --ink-soft:#9B87A0; --card:#fff; --line:#FFD9E8; }
  * { box-sizing:border-box; }
  body { margin:0; color:var(--ink);
         background:linear-gradient(rgba(255,255,255,.32), rgba(255,255,255,.42)),
                    url("/assets/sync-background.png") center center / cover no-repeat fixed;
         font:14px/1.7 "Yuanti SC","YouYuan","Microsoft YaHei UI","Microsoft YaHei",sans-serif; }
  .wrap { max-width:1080px; margin:0 auto; padding:26px 20px 64px; }
  .hero { background:rgba(255,255,255,.82); border-radius:18px; padding:12px 16px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  h1 { font-size:22px; margin:0; color:var(--pink-deep); letter-spacing:1px; }
  .pill { display:inline-block; padding:3px 14px; border-radius:999px; font-size:12px;
          font-weight:600; border:2px solid #fff; box-shadow:0 2px 0 rgba(226,106,155,.18); }
  .pill.running { background:var(--blue-soft); color:#3B7FBF; }
  .pill.done    { background:var(--mint-soft); color:var(--mint-deep); }
  .pill.stopped { background:var(--peach-soft); color:#C97B3D; }
  .pill.error   { background:#FFE3E3; color:#D45A5A; }
  .wrap > .sub { background:rgba(255,255,255,.88); border-radius:14px; padding:10px 14px; }
  .sub { color:var(--ink-soft); font-size:12px; margin:10px 2px 20px; }
  #statline { display:inline-block; margin-top:6px; padding:5px 14px; border-radius:999px;
              background:#fff; border:2px dashed var(--pink); color:var(--pink-deep);
              font-size:12.5px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(152px,1fr)); gap:12px; margin:6px 0 22px; }
  .stat { border-radius:18px; padding:13px 15px; border:2px solid #fff;
          box-shadow:0 4px 0 rgba(226,106,155,.12); }
  .stat:nth-child(4n+1) { background:var(--pink-soft); }
  .stat:nth-child(4n+2) { background:var(--lav-soft); }
  .stat:nth-child(4n+3) { background:var(--mint-soft); }
  .stat:nth-child(4n+4) { background:var(--blue-soft); }
  .stat .k { color:var(--ink-soft); font-size:12px; }
  .stat .v { font-size:19px; font-weight:700; margin-top:2px; color:var(--ink); }
  .card { background:rgba(255,255,255,.90); border:2px solid var(--line); border-radius:22px;
          padding:18px 20px; margin-bottom:20px; box-shadow:0 5px 0 #FFE3EE; }
  .card h2 { font-size:15px; margin:0 0 12px; color:var(--pink-deep); }
  .card h2 .tag { font-size:11px; color:#fff; background:var(--pink); border-radius:999px;
                  padding:2px 10px; margin-left:8px; vertical-align:2px; }
  .bar { height:18px; background:var(--pink-soft); border-radius:999px; overflow:hidden;
         margin:9px 0 5px; border:2px solid #fff; }
  .bar > i { display:block; height:100%; border-radius:999px;
             background:repeating-linear-gradient(115deg,var(--pink) 0 14px,#FFB4D3 14px 26px);
             background-size:200% 100%; animation:candy 1.6s linear infinite;
             transition:width .6s ease; }
  .bar.green > i { background:repeating-linear-gradient(115deg,var(--mint) 0 14px,#A5EBCF 14px 26px); }
  @keyframes candy { from { background-position:0 0; } to { background-position:52px 0; } }
  .meta { color:var(--ink-soft); font-size:12px; display:flex; justify-content:space-between; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  th,td { text-align:left; padding:7px 9px; border-bottom:1.5px dashed var(--line); word-break:break-all; }
  th { color:var(--ink-soft); font-weight:600; }
  tr:hover td { background:#FFF4F9; }
  .ok   { color:var(--mint-deep); }
  .fail { color:#D45A5A; }
  .skip { color:var(--ink-soft); }
  code { background:var(--lav-soft); color:#7A66C2; padding:2px 8px; border-radius:999px;
         font-size:11.5px; border:1.5px solid var(--lav); }
  .foot { color:var(--ink-soft); font-size:12px; text-align:center; margin-top:28px; }
  .mascot { animation:bob 2.6s ease-in-out infinite; }
  @keyframes bob { 0%,100% { transform:translateY(0) rotate(-3deg); }
                   50% { transform:translateY(-5px) rotate(3deg); } }
  .spark { color:var(--pink); font-size:15px; margin:0 2px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <svg class="mascot" width="46" height="46" viewBox="0 0 46 46" aria-hidden="true">
      <path d="M9 16 L7 5 L16 11 Z" fill="#FFB4D3" stroke="#E26A9B" stroke-width="1.6"/>
      <path d="M37 16 L39 5 L30 11 Z" fill="#FFB4D3" stroke="#E26A9B" stroke-width="1.6"/>
      <circle cx="23" cy="25" r="15" fill="#FFD9E8" stroke="#E26A9B" stroke-width="1.8"/>
      <path d="M15 24 q3 -3.6 6 0" fill="none" stroke="#E26A9B" stroke-width="2" stroke-linecap="round"/>
      <path d="M25 24 q3 -3.6 6 0" fill="none" stroke="#E26A9B" stroke-width="2" stroke-linecap="round"/>
      <path d="M21 29 h4 l-2 2.2 Z" fill="#E26A9B"/>
      <path d="M23 31.2 q-2.4 2.6 -5 1.2 M23 31.2 q2.4 2.6 5 1.2" fill="none" stroke="#E26A9B" stroke-width="1.6" stroke-linecap="round"/>
      <path d="M6 27 h6 M6 31 h6 M40 27 h-6 M40 31 h-6" stroke="#E26A9B" stroke-width="1.3" stroke-linecap="round"/>
    </svg>
    <h1>tender 全量同步</h1>
    <span class="spark">✦</span>
    <span id="pill" class="pill running">__STATE_TEXT__</span>
    <span class="spark">✦</span>
  </div>
  <div class="sub">数据源 &lt;TENDER_MYSQL_HOST&gt;（tender_* 库）· 页面每 2 秒自动刷新 ·
    <span id="updated">更新于 __UPDATED__</span> · 已运行 <span id="elapsed">__ELAPSED__</span><br>
    <span id="statline">__STATLINE__</span></div>

  <div class="grid" id="stats">__STATS_HTML__</div>

  <div class="card">
    <h2>业务表导出<span class="tag">mysqldump → sql.gz</span></h2>
    <div class="bar"><i id="tbar" style="width:__TPCT__%"></i></div>
    <div class="meta"><span id="tmeta">__TMETA__</span><span id="tpct">__TPCT__%</span></div>
    <div id="tcurrent" class="sub" style="margin:8px 0 0">__TCURRENT__</div>
  </div>

  <div class="card">
    <h2>文件下载<span class="tag">MinIO 直链</span></h2>
    <div class="bar green"><i id="fbar" style="width:__FPCT__%"></i></div>
    <div class="meta"><span id="fmeta">__FMETA__</span><span id="fpct">__FPCT__%</span></div>
    <div class="bar green" style="margin-top:12px"><i id="bbar" style="width:__BPCT__%"></i></div>
    <div class="meta"><span id="bmeta">__BMETA__</span><span id="bpct">__BPCT__%</span></div>
    <div id="fcurrent" class="sub" style="margin:8px 0 0">__FCURRENT__</div>
  </div>

  <div class="card">
    <h2>最近活动</h2>
    <table><thead><tr><th style="width:150px">时间</th><th style="width:70px">类型</th>
      <th>单元</th><th style="width:90px">状态</th></tr></thead>
      <tbody id="recent">__RECENT_HTML__</tbody></table>
  </div>

  <div class="card">
    <h2>错误<span class="tag">最近 50 条</span></h2>
    <table><thead><tr><th style="width:150px">时间</th><th style="width:70px">类型</th>
      <th style="width:220px">单元</th><th>信息</th></tr></thead>
      <tbody id="errors">__ERRORS_HTML__</tbody></table>
  </div>

  <div class="foot">🐾 中断不怕喵～重跑 <code>python tools/tender_fullsync.py</code> 自动续传（WAL）</div>
</div>
<script>
window.__STATUS__ = __STATUS_JSON__;
function fmtBytes(n){ if(!n) return "0 B";
  const u=["B","KB","MB","GB","TB"]; let i=0; while(n>=1024&&i<u.length-1){n/=1024;i++;}
  return n.toFixed(i?1:0)+" "+u[i]; }
function fmtDur(s){ if(s==null) return "–"; s=Math.max(0,Math.round(s));
  const h=Math.floor(s/3600), m=Math.floor(s%3600/60), x=s%60;
  return h? h+"时"+m+"分" : (m? m+"分"+x+"秒" : x+"秒"); }
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function pct(a,b){ return b>0 ? Math.min(100, a*100/b) : 0; }

function apply(d){
  if(!d || !d.state) return;
  const pill=document.getElementById("pill");
  const map={running:["running","运行中"],done:["done","已完成"],
             stopped:["stopped","已中断"],error:["error","异常"],init:["running","初始化"]};
  const [cls,txt]=map[d.state]||["running",d.state];
  pill.className="pill "+cls; pill.textContent=txt;
  document.getElementById("updated").textContent="更新于 "+(d.updated_at||"–");
  document.getElementById("elapsed").textContent=fmtDur(d.elapsed_s);

  const t=d.tables||{}, f=d.files||{};
  document.getElementById("statline").textContent=
    `阶段 ${d.phase||"–"} ｜ 表 ${((t.done||0)+(t.skip||0))}/${t.total||0} ｜ 文件 ${f.done||0}/${f.total||0}`+
    ` ｜ 失败 ${f.fail||0} ｜ ${f.speed_mbps||0} MB/s ｜ ETA ${fmtDur(f.eta_s)}`;
  document.getElementById("stats").innerHTML=[
    ["阶段", d.phase||"–"],
    ["表 已导/总数", ((t.done||0)+(t.skip||0))+" / "+(t.total||0)],
    ["表 累计行数", (t.rows||0).toLocaleString()],
    ["文件 已下/总数", (f.done||0)+" / "+(f.total||0)],
    ["下载速度", (f.speed_per_min||0)+" 个/分"],
    ["吞吐", (f.speed_mbps||0)+" MB/s"],
    ["已下载", fmtBytes(f.bytes_done)+" / "+fmtBytes(f.bytes_total)],
    ["剩余时间", (f.done||f.fail) ? fmtDur(f.eta_s) : "–"],
  ].map(([k,v])=>`<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  const tp=pct(t.done||0, t.total||0);
  document.getElementById("tbar").style.width=tp+"%";
  document.getElementById("tpct").textContent=tp.toFixed(1)+"%";
  document.getElementById("tmeta").textContent=
    `done ${t.done||0} · skip ${t.skip||0} · fail ${t.failed||0} / total ${t.total||0}`;
  document.getElementById("tcurrent").textContent=
    (t.current&&t.current.length)? "进行中: "+t.current.join("、") : "";

  const fp=pct(f.done||0, f.total||0);
  document.getElementById("fbar").style.width=fp+"%";
  document.getElementById("fpct").textContent=fp.toFixed(1)+"%";
  document.getElementById("fmeta").textContent=
    `ok ${f.ok||0} · skip ${f.skip||0} · fail ${f.fail||0} / total ${f.total||0}`;
  const bp=pct(f.bytes_done||0, f.bytes_total||0);
  document.getElementById("bbar").style.width=bp+"%";
  document.getElementById("bpct").textContent=bp.toFixed(1)+"%";
  document.getElementById("bmeta").textContent=
    `字节进度 ${fmtBytes(f.bytes_done||0)} / ${fmtBytes(f.bytes_total||0)}`;
  document.getElementById("fcurrent").textContent=
    (f.current&&f.current.length)? "进行中: "+f.current.slice(0,6).join("、")+(f.current.length>6?" …":"") : "";

  const st={ok:"ok",done:"ok",skip:"skip",fail:"fail"};
  document.getElementById("recent").innerHTML=(d.recent||[]).slice(-20).reverse().map(r=>
    `<tr><td>${esc(r.ts)}</td><td>${esc(r.unit)}</td><td>${esc(r.key)}</td>`+
    `<td class="${st[r.status]||""}">${esc(r.status)}</td></tr>`).join("")||
    `<tr><td colspan="4" class="skip">暂无</td></tr>`;
  document.getElementById("errors").innerHTML=(d.errors||[]).slice(-20).reverse().map(r=>
    `<tr><td>${esc(r.ts)}</td><td>${esc(r.unit)}</td><td>${esc(r.key)}</td>`+
    `<td class="fail">${esc(r.error)}</td></tr>`).join("")||
    `<tr><td colspan="4" class="ok">无错误</td></tr>`;
}

let failCount = 0, fallback = false;
async function tick(){
  try {
    const d = await (await fetch("/api/status")).json();
    apply(d); failCount = 0;
  } catch(e) {
    failCount++;
    if (failCount >= 3 && !fallback) {
      fallback = true;
      setInterval(()=>location.reload(), 3000);
    }
  }
}
apply(window.__STATUS__);
tick();
setInterval(()=>{ if(!fallback) tick(); }, 2000);
</script>
</body>
</html>
"""

STATE_TEXT = {
    "running": "运行中",
    "done": "已完成",
    "stopped": "已中断",
    "error": "异常",
    "init": "初始化",
}


def _fmt_dur(s):
    if s is None:
        return "–"
    s = max(0, int(s))
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return ("%d时%d分" % (h, m)) if h else (("%d分%d秒" % (m, x)) if m else "%d秒" % x)


def _fmt_bytes(n):
    n = n or 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.1f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
        n /= 1024.0


def _rows_html(records, err=False):
    out = []
    for r in records[-20:][::-1]:
        cls = {"ok": "ok", "done": "ok", "skip": "skip", "fail": "fail"}.get(
            r.get("status", ""), ""
        )
        if err:
            out.append(
                '<tr><td>%s</td><td>%s</td><td>%s</td><td class="fail">%s</td></tr>'
                % (
                    r.get("ts", ""),
                    r.get("unit", ""),
                    r.get("key", ""),
                    (r.get("error", "") or "").replace("<", "&lt;"),
                )
            )
        else:
            out.append(
                '<tr><td>%s</td><td>%s</td><td>%s</td><td class="%s">%s</td></tr>'
                % (
                    r.get("ts", ""),
                    r.get("unit", ""),
                    r.get("key", ""),
                    cls,
                    r.get("status", ""),
                )
            )
    return "".join(out) or (
        '<tr><td colspan="4" class="ok">无错误</td></tr>'
        if err
        else '<tr><td colspan="4" class="skip">暂无</td></tr>'
    )


def render_html(status):
    """把当前进度快照服务端渲染进页面：无 JS/无 fetch 也能看到数据。"""
    t = status.get("tables") or {}
    f = status.get("files") or {}
    tp = min(100.0, (t.get("done", 0) or 0) * 100.0 / max(t.get("total", 0) or 0, 1))
    fp = min(100.0, (f.get("done", 0) or 0) * 100.0 / max(f.get("total", 0) or 0, 1))
    bp = min(
        100.0,
        (f.get("bytes_done", 0) or 0) * 100.0 / max(f.get("bytes_total", 0) or 0, 1),
    )
    stats = [
        ("阶段", status.get("phase") or "–"),
        (
            "表 已导/总数",
            "%s / %s"
            % ((t.get("done", 0) or 0) + (t.get("skip", 0) or 0), t.get("total", 0)),
        ),
        ("表 累计行数", "{:,}".format(t.get("rows", 0) or 0)),
        ("文件 已下/总数", "%s / %s" % (f.get("done", 0), f.get("total", 0))),
        ("下载速度", "%s 个/分" % (f.get("speed_per_min", 0) or 0)),
        ("吞吐", "%s MB/s" % (f.get("speed_mbps", 0) or 0)),
        (
            "已下载",
            "%s / %s"
            % (_fmt_bytes(f.get("bytes_done")), _fmt_bytes(f.get("bytes_total"))),
        ),
        ("剩余时间", _fmt_dur(f.get("eta_s"))),
    ]
    stats_html = "".join(
        '<div class="stat"><div class="k">%s</div><div class="v">%s</div></div>'
        % (k, v)
        for k, v in stats
    )
    statline = "阶段 %s ｜ 表 %s/%s ｜ 文件 %s/%s ｜ 失败 %s ｜ %s MB/s ｜ ETA %s" % (
        status.get("phase") or "–",
        (t.get("done", 0) or 0) + (t.get("skip", 0) or 0),
        t.get("total", 0),
        f.get("done", 0),
        f.get("total", 0),
        f.get("fail", 0),
        f.get("speed_mbps", 0) or 0,
        _fmt_dur(f.get("eta_s")),
    )
    tcurrent = (
        "进行中: " + "、".join(t.get("current", [])[:6]) if t.get("current") else ""
    )
    fcurrent = (
        "进行中: " + "、".join(f.get("current", [])[:6]) if f.get("current") else ""
    )
    tmeta = "done %s · skip %s · fail %s / total %s" % (
        t.get("done", 0),
        t.get("skip", 0),
        t.get("failed", 0),
        t.get("total", 0),
    )
    fmeta = "ok %s · skip %s · fail %s / total %s" % (
        f.get("ok", 0),
        f.get("skip", 0),
        f.get("fail", 0),
        f.get("total", 0),
    )
    bmeta = "字节进度 %s / %s" % (
        _fmt_bytes(f.get("bytes_done")),
        _fmt_bytes(f.get("bytes_total")),
    )
    status_json = json.dumps(status, ensure_ascii=False).replace("</", "<\\/")
    html = (
        HTML.replace("__STATUS_JSON__", status_json)
        .replace("__STATE_TEXT__", STATE_TEXT.get(status.get("state"), "初始化"))
        .replace("__UPDATED__", status.get("updated_at") or "–")
        .replace("__ELAPSED__", _fmt_dur(status.get("elapsed_s")))
        .replace("__STATLINE__", statline)
        .replace("__STATS_HTML__", stats_html)
        .replace("__TPCT__", "%.1f" % tp)
        .replace("__FPCT__", "%.1f" % fp)
        .replace("__BPCT__", "%.1f" % bp)
        .replace("__TMETA__", tmeta)
        .replace("__FMETA__", fmeta)
        .replace("__BMETA__", bmeta)
        .replace("__TCURRENT__", tcurrent)
        .replace("__FCURRENT__", fcurrent)
        .replace("__RECENT_HTML__", _rows_html(status.get("recent") or []))
        .replace("__ERRORS_HTML__", _rows_html(status.get("errors") or [], err=True))
    )
    return html


def load_status(progress_path):
    try:
        with open(progress_path, "rb") as f:
            status = json.loads(f.read())
        if not isinstance(status, dict):
            raise ValueError("bad snapshot")
        return status
    except Exception:
        return {
            "state": "init",
            "phase": "waiting",
            "updated_at": "–",
            "tables": {},
            "files": {},
            "errors": [],
            "recent": [],
        }


class Handler(BaseHTTPRequestHandler):
    progress_path = DEFAULT_PROGRESS

    def log_message(self, *a):  # 静默访问日志
        pass

    def _send(self, code, ctype, payload):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            status = load_status(self.progress_path)
            self._send(200, "text/html", render_html(status).encode("utf-8"))
        elif path == "/api/status":
            try:
                with open(self.progress_path, "rb") as f:
                    data = f.read()
                json.loads(data)  # 校验完整性（原子写，读到的要么完整要么旧版）
            except Exception:
                data = json.dumps(
                    {
                        "state": "init",
                        "phase": "waiting",
                        "tables": {},
                        "files": {},
                        "errors": [],
                        "recent": [],
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
            self._send(200, "application/json", data)
        elif path == "/assets/sync-background.png":
            try:
                with open(BG_PATH, "rb") as f:
                    payload = f.read()
                self._send(200, "image/png", payload)
            except OSError:
                self._send(404, "text/plain", b"not found")
        else:
            self._send(404, "text/plain", b"not found")


def start_in_thread(progress_path=DEFAULT_PROGRESS, port=8770, host="127.0.0.1"):
    """启动进度 Web（daemon 线程），端口占用自动 +1 重试，返回实际端口。"""
    Handler.progress_path = progress_path
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, p), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise RuntimeError("端口 %d-%d 全部被占用" % (port, port + 19))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


def main():
    ap = argparse.ArgumentParser(description="tender 同步进度 Web")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--progress", default=DEFAULT_PROGRESS)
    args = ap.parse_args()
    port = start_in_thread(args.progress, args.port, args.host)
    print("进度页: http://%s:%d/  （Ctrl+C 退出）" % (args.host, port))
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
