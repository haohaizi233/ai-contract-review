# -*- coding: utf-8 -*-
"""tender_case_web.py — 新库三件套导出实时进度 Web（零第三方依赖，stdlib http.server）。

- `/`          实时进度页（卡哇伊粉绿风，复用 tender_sync_web 设计语言与背景）。
               展示阶段流程、案件总数、已写/未变/失败数、补抓进度、三件套覆盖率指标、缺口统计、最近活动与错误列表。
- `/api/status` 进度 JSON（直接透传 _progress.json 快照）

用法
  独立运行: python tools/tender_case_web.py [--port 8771] [--progress data/tender-case-export/_progress.json]
  内嵌运行: tender_case_export.py 自动后台启动（start_in_thread）
"""

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
DEFAULT_PROGRESS = os.path.join(ROOT, "data", "tender-case-export", "_progress.json")
BG_PATH = os.path.join(ROOT, "tools", "assets", "sync-background.png")

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<noscript><meta http-equiv="refresh" content="5"></noscript>
<title>新库三件套 Markdown 导出 · 实时进度</title>
<style>
  :root { --pink:#FF9EC4; --pink-deep:#E26A9B; --pink-soft:#FFEAF2; --mint:#7EDDBB;
          --mint-deep:#2FA37E; --mint-soft:#E7F8F1; --lav:#C9BDF6; --lav-soft:#F1EDFF;
          --blue:#8FC7F5; --blue-soft:#E7F3FF; --peach:#FFC9A3; --peach-soft:#FFF0E4;
          --ink:#5B4A5E; --ink-soft:#9B87A0; --card:#fff; --line:#FFD9E8; }
  * { box-sizing:border-box; }
  body { margin:0; color:var(--ink);
         background:linear-gradient(rgba(255,255,255,.36), rgba(255,255,255,.46)),
                    url("/assets/sync-background.png") center center / cover no-repeat fixed;
         font:14px/1.7 "Yuanti SC","YouYuan","Microsoft YaHei UI","Microsoft YaHei",sans-serif; }
  .wrap { max-width:1120px; margin:0 auto; padding:26px 20px 64px; }
  .hero { background:rgba(255,255,255,.88); border-radius:18px; padding:12px 18px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; border:2px solid #fff; box-shadow:0 4px 12px rgba(226,106,155,.12); }
  h1 { font-size:22px; margin:0; color:var(--pink-deep); letter-spacing:1px; }
  .pill { display:inline-block; padding:3px 14px; border-radius:999px; font-size:12px;
          font-weight:600; border:2px solid #fff; box-shadow:0 2px 0 rgba(226,106,155,.18); }
  .pill.running { background:var(--blue-soft); color:#3B7FBF; }
  .pill.done    { background:var(--mint-soft); color:var(--mint-deep); }
  .pill.stopped { background:var(--peach-soft); color:#C97B3D; }
  .pill.error   { background:#FFE3E3; color:#D45A5A; }
  .sub { color:var(--ink-soft); font-size:12px; margin:10px 2px 20px; background:rgba(255,255,255,.88); border-radius:14px; padding:10px 14px; }
  #statline { display:inline-block; margin-top:6px; padding:5px 14px; border-radius:999px;
              background:#fff; border:2px dashed var(--pink); color:var(--pink-deep);
              font-size:12.5px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin:6px 0 22px; }
  .stat { border-radius:18px; padding:12px 14px; border:2px solid #fff;
          box-shadow:0 4px 0 rgba(226,106,155,.12); background:var(--card); }
  .stat:nth-child(5n+1) { background:var(--pink-soft); }
  .stat:nth-child(5n+2) { background:var(--lav-soft); }
  .stat:nth-child(5n+3) { background:var(--mint-soft); }
  .stat:nth-child(5n+4) { background:var(--blue-soft); }
  .stat:nth-child(5n+5) { background:var(--peach-soft); }
  .stat .k { color:var(--ink-soft); font-size:12px; }
  .stat .v { font-size:18px; font-weight:700; margin-top:2px; color:var(--ink); }
  .card { background:rgba(255,255,255,.92); border:2px solid var(--line); border-radius:22px;
          padding:18px 20px; margin-bottom:20px; box-shadow:0 5px 0 #FFE3EE; }
  .card h2 { font-size:15px; margin:0 0 12px; color:var(--pink-deep); display:flex; align-items:center; }
  .card h2 .tag { font-size:11px; color:#fff; background:var(--pink); border-radius:999px;
                  padding:2px 10px; margin-left:8px; vertical-align:2px; }
  .steps-bar { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
  .step-chip { padding:6px 12px; border-radius:12px; font-size:12px; background:#fff; border:1.5px solid var(--line); display:flex; align-items:center; gap:6px; }
  .step-chip.running { border-color:var(--blue); background:var(--blue-soft); font-weight:600; }
  .step-chip.done { border-color:var(--mint-deep); background:var(--mint-soft); color:var(--mint-deep); }
  .step-chip.skip { color:var(--ink-soft); background:#F5F5F7; }
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
    <svg class="mascot" width="44" height="44" viewBox="0 0 46 46" aria-hidden="true">
      <path d="M9 16 L7 5 L16 11 Z" fill="#FFB4D3" stroke="#E26A9B" stroke-width="1.6"/>
      <path d="M37 16 L39 5 L30 11 Z" fill="#FFB4D3" stroke="#E26A9B" stroke-width="1.6"/>
      <circle cx="23" cy="25" r="15" fill="#FFD9E8" stroke="#E26A9B" stroke-width="1.8"/>
      <path d="M15 24 q3 -3.6 6 0" fill="none" stroke="#E26A9B" stroke-width="2" stroke-linecap="round"/>
      <path d="M25 24 q3 -3.6 6 0" fill="none" stroke="#E26A9B" stroke-width="2" stroke-linecap="round"/>
      <path d="M21 29 h4 l-2 2.2 Z" fill="#E26A9B"/>
      <path d="M23 31.2 q-2.4 2.6 -5 1.2 M23 31.2 q2.4 2.6 5 1.2" fill="none" stroke="#E26A9B" stroke-width="1.6" stroke-linecap="round"/>
    </svg>
    <h1>新库三件套 Markdown 导出</h1>
    <span class="spark">✦</span>
    <span id="pill" class="pill running">__STATE_TEXT__</span>
    <span class="spark">✦</span>
  </div>
  <div class="sub">
    产物路径: <code>data/tender-case-export/</code> · 页面自动 2s 刷新 ·
    <span id="updated">更新于 __UPDATED__</span> · 已运行 <span id="elapsed">__ELAPSED__</span><br>
    <span id="statline">__STATLINE__</span>
  </div>

  <div class="card">
    <h2>执行步骤</h2>
    <div class="steps-bar" id="steps">__STEPS_HTML__</div>
  </div>

  <div class="grid" id="stats">__STATS_HTML__</div>

  <div class="card">
    <h2>Markdown 渲染进度<span class="tag">cases/<前缀>/<编号>__apply_<ID>.md</span></h2>
    <div class="bar"><i id="cbar" style="width:__CPCT__%"></i></div>
    <div class="meta"><span id="cmeta">__CMETA__</span><span id="cpct">__CPCT__%</span></div>
  </div>

  <div class="card">
    <h2>附件 / 图片补抓<span class="tag">tender_infra + 审批图片</span></h2>
    <div class="bar green"><i id="fbar" style="width:__FPCT__%"></i></div>
    <div class="meta"><span id="fmeta">__FMETA__</span><span id="fpct">__FPCT__%</span></div>
  </div>

  <div class="card" id="card_cover">
    <h2>三件套覆盖率与缺口统计</h2>
    <div id="cover_info" style="font-size:13px; line-height:1.8;">__COVER_HTML__</div>
  </div>

  <div class="card">
    <h2>最近活动</h2>
    <table><thead><tr><th style="width:150px">时间</th><th style="width:70px">类型</th>
      <th>单元</th><th style="width:110px">状态</th></tr></thead>
      <tbody id="recent">__RECENT_HTML__</tbody></table>
  </div>

  <div class="card">
    <h2>错误 / 缺口提示<span class="tag">最近 50 条</span></h2>
    <table><thead><tr><th style="width:150px">时间</th><th style="width:70px">类型</th>
      <th style="width:200px">单元</th><th>信息</th></tr></thead>
      <tbody id="errors">__ERRORS_HTML__</tbody></table>
  </div>

  <div class="foot">🌸 导出脚本支持原子断点续写与哈希比对，再次运行会自动跳过已生成的相同文档 🌸</div>
</div>
<script>
window.__STATUS__ = __STATUS_JSON__;
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

  const c=d.cases||{}, f=d.fetch||{};
  document.getElementById("statline").textContent=
    `阶段: ${d.phase||"–"} ｜ 案件 ${c.done||0}/${c.total||0} (写 ${c.written||0} / 存 ${c.unchanged||0}) ｜ 补抓 ${f.done||0}/${f.total||0} ｜ 速度 ${c.speed_per_min||0} 案/分 ｜ ETA ${fmtDur(c.eta_s)}`;

  if (d.steps) {
    document.getElementById("steps").innerHTML = d.steps.map((s, i) => {
      const icon = s.status === "done" ? "✔" : (s.status === "running" ? "⏳" : (s.status === "skip" ? "↷" : "○"));
      return `<div class="step-chip ${s.status}"><span>${icon}</span> <b>${i+1}. ${esc(s.name)}</b> <span style="color:var(--ink-soft)">${esc(s.detail||"")}</span></div>`;
    }).join("");
  }

  document.getElementById("stats").innerHTML=[
    ["当前阶段", d.phase||"–"],
    ["案件进度", (c.done||0)+" / "+(c.total||0)],
    ["已写文件", (c.written||0)+" 份"],
    ["已跳过(未变)", (c.unchanged||0)+" 份"],
    ["案件失败", (c.fail||0)+" 件"],
    ["生成速度", (c.speed_per_min||0)+" 案/分"],
    ["剩余时间", fmtDur(c.eta_s)],
    ["补抓附件/图片", (f.ok||0)+" 成功 / "+(f.fail||0)+" 失败"],
  ].map(([k,v])=>`<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  const cp=pct(c.done||0, c.total||0);
  document.getElementById("cbar").style.width=cp+"%";
  document.getElementById("cpct").textContent=cp.toFixed(1)+"%";
  document.getElementById("cmeta").textContent=`done ${c.done||0} · written ${c.written||0} · unchanged ${c.unchanged||0} · fail ${c.fail||0} / total ${c.total||0}`;

  const fp=pct(f.done||0, f.total||0);
  document.getElementById("fbar").style.width=fp+"%";
  document.getElementById("fpct").textContent=fp.toFixed(1)+"%";
  document.getElementById("fmeta").textContent=`ok ${f.ok||0} · skip ${f.skip||0} · fail ${f.fail||0} / total ${f.total||0}`;

  if (d.cover) {
    const cv = d.cover;
    let ch = "";
    if (cv.totals) {
      ch += `<div><b>汇总指标：</b>审批意见共 ${cv.totals.comments||0} 条 ｜ 事件记录 ${cv.totals.events||0} 条 ｜ 关联附件 ${cv.totals.files||0} 个 (已落盘 ${cv.totals.files_local||0}) ｜ 审批图片 ${cv.totals.images||0} 张 (已本地化 ${cv.totals.images_local||0})</div>`;
    }
    if (cv.attach) {
      ch += `<div style="margin-top:4px;"><b>附件覆盖：</b>${Object.entries(cv.attach).map(([k,v])=>`${k}: <b>${v}</b>`).join(" ｜ ")}</div>`;
    }
    if (cv.flow) {
      ch += `<div style="margin-top:4px;"><b>流程匹配：</b>${Object.entries(cv.flow).map(([k,v])=>`${k}: <b>${v}</b>`).join(" ｜ ")}</div>`;
    }
    if (cv.gap_kinds) {
      ch += `<div style="margin-top:4px;"><b>主要缺口类型：</b>${Object.entries(cv.gap_kinds).slice(0,8).map(([k,v])=>`<span style="color:#D45A5A;">${k}</span>(${v})`).join("、 ")}</div>`;
    }
    document.getElementById("cover_info").innerHTML = ch || "正在统计三件套覆盖分布…";
  }

  const st={ok:"ok",written:"ok",skip:"skip",fail:"fail"};
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


def render_html(status):
    c = status.get("cases") or {}
    f = status.get("fetch") or {}
    cp = min(100.0, (c.get("done", 0) or 0) * 100.0 / max(c.get("total", 0) or 0, 1))
    fp = min(100.0, (f.get("done", 0) or 0) * 100.0 / max(f.get("total", 0) or 0, 1))

    steps_html = "".join(
        '<div class="step-chip %s"><span>%s</span> <b>%d. %s</b> <span style="color:var(--ink-soft)">%s</span></div>'
        % (
            s.get("status", ""),
            "✔"
            if s.get("status") == "done"
            else ("⏳" if s.get("status") == "running" else "○"),
            i + 1,
            s.get("name", ""),
            s.get("detail", "") or "",
        )
        for i, s in enumerate(status.get("steps") or [])
    )

    stats = [
        ("当前阶段", status.get("phase") or "–"),
        ("案件进度", "%s / %s" % (c.get("done", 0), c.get("total", 0))),
        ("已写文件", "%s 份" % (c.get("written", 0))),
        ("已跳过(未变)", "%s 份" % (c.get("unchanged", 0))),
        ("案件失败", "%s 件" % (c.get("fail", 0))),
        ("生成速度", "%s 案/分" % (c.get("speed_per_min", 0) or 0)),
        ("剩余时间", _fmt_dur(c.get("eta_s"))),
        ("补抓附件/图片", "%s 成功 / %s 失败" % (f.get("ok", 0), f.get("fail", 0))),
    ]
    stats_html = "".join(
        '<div class="stat"><div class="k">%s</div><div class="v">%s</div></div>'
        % (k, v)
        for k, v in stats
    )

    statline = "阶段: %s ｜ 案件 %s/%s ｜ 补抓 %s/%s ｜ 速度 %s 案/分 ｜ ETA %s" % (
        status.get("phase") or "–",
        c.get("done", 0),
        c.get("total", 0),
        f.get("done", 0),
        f.get("total", 0),
        c.get("speed_per_min", 0) or 0,
        _fmt_dur(c.get("eta_s")),
    )

    cmeta = "done %s · written %s · unchanged %s · fail %s / total %s" % (
        c.get("done", 0),
        c.get("written", 0),
        c.get("unchanged", 0),
        c.get("fail", 0),
        c.get("total", 0),
    )
    fmeta = "ok %s · skip %s · fail %s / total %s" % (
        f.get("ok", 0),
        f.get("skip", 0),
        f.get("fail", 0),
        f.get("total", 0),
    )

    cv = status.get("cover") or {}
    cover_html = ""
    if cv.get("totals"):
        tot = cv["totals"]
        cover_html += (
            "<div><b>汇总指标：</b>审批意见共 %s 条 ｜ 事件记录 %s 条 ｜ 关联附件 %s 个 (已落盘 %s) ｜ 审批图片 %s 张 (已本地化 %s)</div>"
            % (
                tot.get("comments", 0),
                tot.get("events", 0),
                tot.get("files", 0),
                tot.get("files_local", 0),
                tot.get("images", 0),
                tot.get("images_local", 0),
            )
        )

    recent_html = (
        "".join(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td class="%s">%s</td></tr>'
            % (
                r.get("ts", ""),
                r.get("unit", ""),
                r.get("key", ""),
                "ok"
                if r.get("status") in ("ok", "written")
                else ("skip" if r.get("status") == "skip" else "fail"),
                r.get("status", ""),
            )
            for r in (status.get("recent") or [])[-20:][::-1]
        )
        or '<tr><td colspan="4" class="skip">暂无</td></tr>'
    )

    errors_html = (
        "".join(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td class="fail">%s</td></tr>'
            % (
                r.get("ts", ""),
                r.get("unit", ""),
                r.get("key", ""),
                (r.get("error", "") or "").replace("<", "&lt;"),
            )
            for r in (status.get("errors") or [])[-20:][::-1]
        )
        or '<tr><td colspan="4" class="ok">无错误</td></tr>'
    )

    status_json = json.dumps(status, ensure_ascii=False).replace("</", "<\\/")
    return (
        HTML.replace("__STATUS_JSON__", status_json)
        .replace("__STATE_TEXT__", STATE_TEXT.get(status.get("state"), "初始化"))
        .replace("__UPDATED__", status.get("updated_at") or "–")
        .replace("__ELAPSED__", _fmt_dur(status.get("elapsed_s")))
        .replace("__STATLINE__", statline)
        .replace("__STEPS_HTML__", steps_html)
        .replace("__STATS_HTML__", stats_html)
        .replace("__CPCT__", "%.1f" % cp)
        .replace("__FPCT__", "%.1f" % fp)
        .replace("__CMETA__", cmeta)
        .replace("__FMETA__", fmeta)
        .replace("__COVER_HTML__", cover_html or "正在统计中…")
        .replace("__RECENT_HTML__", recent_html)
        .replace("__ERRORS_HTML__", errors_html)
    )


class Handler(BaseHTTPRequestHandler):
    progress_path = DEFAULT_PROGRESS

    def log_message(self, *a):
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
            try:
                st = json.loads(open(self.progress_path, "rb").read())
            except Exception:
                st = {"state": "init", "phase": "waiting"}
            self._send(200, "text/html", render_html(st).encode("utf-8"))
        elif path == "/api/status":
            try:
                data = open(self.progress_path, "rb").read()
                json.loads(data)
            except Exception:
                data = b'{"state":"init"}'
            self._send(200, "application/json", data)
        elif path == "/assets/sync-background.png":
            try:
                self._send(200, "image/png", open(BG_PATH, "rb").read())
            except OSError:
                self._send(404, "text/plain", b"not found")
        else:
            self._send(404, "text/plain", b"not found")


def start_in_thread(progress_path=DEFAULT_PROGRESS, port=8771, host="127.0.0.1"):
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--progress", default=DEFAULT_PROGRESS)
    args = ap.parse_args()
    port = start_in_thread(args.progress, args.port)
    print("进度页: http://127.0.0.1:%d/ (Ctrl+C 退出)" % port)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
