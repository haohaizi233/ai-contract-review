import os
import re
import json
import subprocess
import threading
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

desktop_dir = r"d:\Users\Administrator\Desktop"
md_path = os.path.join(desktop_dir, "AI合同_RAG切分与召回策略.md")
temp_dir = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "rag_pdf_assets")
rendered_html = os.path.join(desktop_dir, "AI合同_RAG切分与召回策略.html")
output_pdf = os.path.join(desktop_dir, "AI合同_RAG切分与召回策略.pdf")
edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

print(f"Reading markdown: {md_path}")
with open(md_path, "r", encoding="utf-8") as f:
    md_content = f.read()

with open(os.path.join(temp_dir, "marked.min.js"), "r", encoding="utf-8") as f:
    marked_js = f.read()
with open(os.path.join(temp_dir, "mermaid.min.js"), "r", encoding="utf-8") as f:
    mermaid_js = f.read()
with open(os.path.join(temp_dir, "katex.min.js"), "r", encoding="utf-8") as f:
    katex_js = f.read()
with open(os.path.join(temp_dir, "katex.min.css"), "r", encoding="utf-8") as f:
    katex_css = f.read()

converter_template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>AI合同项目 RAG 切分与召回策略说明</title>
<style>
/* KaTeX CSS */
__KATEX_CSS__

/* Print & Screen Typography CSS */
@page {
    size: A4 portrait;
    margin: 16mm 14mm 16mm 14mm;
}

* {
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: 9.5pt;
    line-height: 1.55;
    color: #24292f;
    background-color: #ffffff;
    margin: 0;
    padding: 10px 20px;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}

h1 {
    font-size: 18pt;
    font-weight: 700;
    color: #0969da;
    border-bottom: 2px solid #0969da;
    padding-bottom: 8px;
    margin-top: 10px;
    margin-bottom: 14px;
    page-break-after: avoid;
}

h2 {
    font-size: 13.5pt;
    font-weight: 600;
    color: #1f2328;
    border-bottom: 1px solid #d0d7de;
    padding-bottom: 5px;
    margin-top: 22px;
    margin-bottom: 10px;
    page-break-after: avoid;
}

h3 {
    font-size: 11.5pt;
    font-weight: 600;
    color: #1f2328;
    margin-top: 16px;
    margin-bottom: 8px;
    page-break-after: avoid;
}

h4 {
    font-size: 10pt;
    font-weight: 600;
    color: #333;
    margin-top: 12px;
    margin-bottom: 6px;
    page-break-after: avoid;
}

p, ul, ol {
    margin-top: 0;
    margin-bottom: 8px;
}

li {
    margin-bottom: 3px;
}

blockquote {
    margin: 8px 0;
    padding: 6px 14px;
    color: #57606a;
    background-color: #f6f8fa;
    border-left: 4px solid #0969da;
    border-radius: 0 4px 4px 0;
    page-break-inside: avoid;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 8.5pt;
    page-break-inside: avoid;
}

th, td {
    border: 1px solid #d0d7de;
    padding: 5px 8px;
    text-align: left;
    vertical-align: middle;
}

th {
    background-color: #f6f8fa;
    font-weight: 600;
    color: #24292f;
}

tr:nth-child(even) {
    background-color: #fafbfc;
}

code {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    font-size: 8pt;
    background-color: rgba(175, 184, 193, 0.2);
    padding: 2px 4px;
    border-radius: 4px;
    color: #b31d28;
}

pre {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    font-size: 7.8pt;
    background-color: #f6f8fa;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    padding: 8px;
    overflow-x: auto;
    page-break-inside: avoid;
    line-height: 1.35;
}

pre code {
    background-color: transparent;
    padding: 0;
    color: #24292f;
}

.mermaid-wrapper {
    margin: 14px 0;
    padding: 10px;
    background-color: #ffffff;
    border: 1px solid #e1e4e8;
    border-radius: 6px;
    text-align: center;
    page-break-inside: avoid;
    overflow-x: auto;
}

.mermaid-wrapper svg {
    max-width: 100% !important;
    height: auto !important;
    display: inline-block;
}

.katex-display {
    margin: 10px 0;
    text-align: center;
    page-break-inside: avoid;
}

hr {
    height: 1px;
    background-color: #d0d7de;
    border: none;
    margin: 16px 0;
}
</style>
<script>
__MARKED_JS__
</script>
<script>
__MERMAID_JS__
</script>
<script>
__KATEX_JS__
</script>
</head>
<body>
<div id="content"></div>
<script>
const rawMd = __RAW_MD_JSON__;

function sendLog(msg) {
    fetch('/log', { method: 'POST', body: msg });
}

async function renderDocument() {
    try {
        sendLog('Starting renderDocument');
        mermaid.initialize({
            startOnLoad: false,
            theme: 'default',
            securityLevel: 'loose',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
            flowchart: {
                htmlLabels: true,
                curve: 'basis'
            }
        });

        const renderer = new marked.Renderer();
        let mermaidCounter = 0;
        const mermaidCodes = [];

        function escapeHtml(text) {
            return String(text || '')
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        renderer.code = function(token, lang) {
            const code = (typeof token === 'object' && token !== null) ? token.text : token;
            const language = (typeof token === 'object' && token !== null) ? token.lang : lang;
            if (language === 'mermaid') {
                const id = 'mermaid-block-' + (mermaidCounter++);
                mermaidCodes.push({ id, code: code });
                return '<div class="mermaid-wrapper"><div id="' + id + '"></div></div>';
            }
            return '<pre><code class="language-' + (language || '') + '">' + escapeHtml(code) + '</code></pre>';
        };

        marked.setOptions({
            renderer: renderer,
            gfm: true,
            breaks: false
        });

        // KaTeX formulas
        let processedMd = rawMd.replace(/\\$\\$([\\s\\S]*?)\\$\\$/g, function(match, tex) {
            try {
                return '<div class="katex-display">' + katex.renderToString(tex.trim(), { displayMode: true, throwOnError: false }) + '</div>';
            } catch(e) {
                return match;
            }
        });

        processedMd = processedMd.replace(/\\$([^$\\n]+?)\\$/g, function(match, tex) {
            try {
                return katex.renderToString(tex.trim(), { displayMode: false, throwOnError: false });
            } catch(e) {
                return match;
            }
        });

        const html = marked.parse(processedMd);
        document.getElementById('content').innerHTML = html;
        sendLog('Markdown parsed into HTML. Rendering ' + mermaidCodes.length + ' mermaid diagrams...');

        for (let i = 0; i < mermaidCodes.length; i++) {
            const item = mermaidCodes[i];
            try {
                const container = document.getElementById(item.id);
                if (container) {
                    const renderId = 'svg-' + item.id;
                    const res = await mermaid.render(renderId, item.code);
                    container.innerHTML = res.svg;
                }
            } catch (err) {
                sendLog('Mermaid render error for ' + item.id + ': ' + (err.message || err));
                const container = document.getElementById(item.id);
                if (container) {
                    container.innerHTML = '<div style="color:red;padding:10px;border:1px solid red;">Mermaid渲染异常: ' + (err.message || err) + '<pre>' + item.code + '</pre></div>';
                }
            }
        }

        sendLog('All mermaid rendered! Posting outerHTML back...');
        const finalHtml = '<!DOCTYPE html>\\n' + document.documentElement.outerHTML;
        await fetch('/save', {
            method: 'POST',
            headers: { 'Content-Type': 'text/html; charset=utf-8' },
            body: finalHtml
        });
        sendLog('outerHTML posted successfully!');
    } catch(err) {
        sendLog('Global render error: ' + (err.message || err));
    }
}

renderDocument();
</script>
</body>
</html>
"""

converter_html_content = (
    converter_template.replace("__KATEX_CSS__", katex_css)
    .replace("__MARKED_JS__", marked_js)
    .replace("__MERMAID_JS__", mermaid_js)
    .replace("__KATEX_JS__", katex_js)
    .replace("__RAW_MD_JSON__", json.dumps(md_content))
)

rendered_event = threading.Event()
received_html = []


class LocalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(converter_html_content.encode("utf-8"))

    def do_POST(self):
        if self.path == "/log":
            content_len = int(self.headers.get("Content-Length", 0))
            msg = self.rfile.read(content_len).decode("utf-8", errors="ignore")
            print(f"[Browser Log] {msg}")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        elif self.path == "/save":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8")
            received_html.append(post_body)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            rendered_event.set()

    def log_message(self, format, *args):
        pass


PORT = 18999
server = HTTPServer(("127.0.0.1", PORT), LocalHandler)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()
print(f"Local server started on http://127.0.0.1:{PORT}")

print("Step 1: Launching Edge with --dump-dom to trigger renderDocument()...")
cmd_edge = [
    edge_path,
    "--headless=new",
    "--disable-gpu",
    "--virtual-time-budget=30000",
    "--dump-dom",
    f"http://127.0.0.1:{PORT}",
]

res_edge = subprocess.run(
    cmd_edge, capture_output=True, text=True, encoding="utf-8", errors="ignore"
)

# Wait up to 10 more seconds if not already set
if not rendered_event.is_set():
    print("Waiting for save signal...")
    rendered_event.wait(timeout=10)

server.shutdown()

if not received_html:
    print("ERROR: Did not receive rendered HTML from Edge!")
    sys.exit(1)

full_html = received_html[0]
print(f"Received rendered HTML ({len(full_html)} chars)")

# Clean up script tags from the static HTML
clean_html = re.sub(r"<script[\s\S]*?</script>", "", full_html)

print(f"Saving static HTML to: {rendered_html}")
with open(rendered_html, "w", encoding="utf-8") as f:
    f.write(clean_html)

# Step 2: Print static HTML to PDF using Edge
print("Step 2: Printing static HTML to PDF via Edge...")
cmd_pdf = [
    edge_path,
    "--headless=new",
    "--disable-gpu",
    "--no-pdf-header-footer",
    f"--print-to-pdf={output_pdf}",
    "file:///" + rendered_html.replace("\\", "/"),
]

res_pdf = subprocess.run(
    cmd_pdf, capture_output=True, text=True, encoding="utf-8", errors="ignore"
)
print(res_pdf.stdout or "")

if os.path.exists(output_pdf) and os.path.getsize(output_pdf) > 20000:
    size_kb = os.path.getsize(output_pdf) / 1024
    print(
        f"SUCCESS: PDF created successfully!\nPath: {output_pdf}\nSize: {size_kb:.1f} KB"
    )
else:
    sz = os.path.getsize(output_pdf) if os.path.exists(output_pdf) else 0
    print(f"ERROR: PDF size is {sz} bytes.")
    sys.exit(1)
