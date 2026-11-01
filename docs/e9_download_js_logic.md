# e9（hc.hcipi.com:8088）附件下载 / 鉴权 JS 逻辑落盘

> 抓取方式：CDP `Runtime.evaluate` 于 `http://hc.hcipi.com:8088/wui/index.html` 页面上下文，
> 直接 fetch 同源静态 JS 资源后正则定位。落盘时间：2026-11-01。

## 1. 结论（对下载器的指导）

1. **前端附件下载就是普通 GET**：`GET /weaver/weaver.file.FileDownload?fileid=<imagefileid>`，
   鉴权完全依赖会话 Cookie（含 HttpOnly 的 `JSESSIONID` / `ecology_JSessionid`）。
   上传/附件组件的 `download(e)` 实现即 `window.open(url)`（Chrome>=73）或 iframe.src=url。
2. **`downloadFile(e, t, n)` 工具函数**（`cloudstore/resource/pc/com/v1/ecCom.min.js` @45014）：
   用隐藏表单提交下载，支持附加参数。若 `localStorage.access_token` 存在，会自动带上
   `access_token` 参数 —— 说明**除 Cookie 外还存在 access_token 鉴权通道**，登录后可验证
   `FileDownload?fileid=N&access_token=...` 是否免 Cookie 可用。
3. **登录接口对密码做 RSA 加密**（`/js/weaver_encrypt/weaver_encrypt.js` + `jsencrypt_wev.js`），
   且登录页有验证码，无法脚本化登录，必须人工登录一次刷新会话。
4. 会话未登录时，所有 `/api/**` 返回 `{"msg":"登录信息超时","errorCode":"002","status":false}`；
   `FileDownload` 返回 235B 的 `<script type='text/javascript'>…` 跳登录页 HTML（被旧下载器误存为假附件）。

## 2. `downloadFile` 原文（ecCom.min.js:45014，反混淆整理）

```js
downloadFile: function (url, method, params) {
    params || (params = {});
    if ("string" == typeof localStorage.access_token && "" !== localStorage.access_token) {
        params.access_token = localStorage.access_token;
    }
    url = new RegExp("^" + window.ecologyContentPath).test(url)
        ? url
        : (window.ecologyContentPath || "") + url;
    url = "" + PE_PREFIX + url;   // pe 为域名/前缀拼接变量
    var form = jQuery("<form></form>").attr("action", url).attr("method", method);
    var inputs = "";
    for (var k in params) {
        inputs += '<input type="hidden" name="' + k + '" value="' + params[k] + '" />';
    }
    inputs += '<input type="hidden" name="testHolder" value="testHolder" />';
    form.append(inputs).appendTo("body").submit().remove();
}
```

## 3. 附件组件 `download` 原文（ecCom.min.js:480763，反混淆整理）

```js
{ key: "download", value: function (url, e) {
    if (e && e.stopPropagation && e.stopPropagation(),
        "function" == typeof this.props.onCustomDownload) return void this.props.onCustomDownload(url);
    var n = this.props.uploadId,
        o = this.state.containerId,
        r = void 0 === o ? "" : o,
        i = this.refs["download_" + n + "_" + r];
    if (i) {
        var a = window.navigator.userAgent;
        url = new RegExp("^" + window.ecologyContentPath).test(url)
            ? url
            : (window.ecologyContentPath || "") + url;
        Number(a.replace(/^.*Chrome\/([\d]+).*$/, "$1")) >= 73
            ? window.open(url)
            : (i.src = "", i.src = url);   // iframe 下载
    }
} }
```

配套 API：重命名附件走 `POST /api/doc/acc/rename {imagefileid, imagefilename}`；
水印图 URL 形如 `/weaver/weaver.file.FileDownload?fileid=<imageid>`（与附件同一入口）。

## 4. 鉴权状态实测（2026-11-01 08:50）

- `document.cookie` 为空 → 关键会话 Cookie 全部 HttpOnly，JS 读不到；
- `fetch('/api/portal/quickSearch/getQuickSearchTypes')` → `登录信息超时 errorCode=002`；
- `fetch('/weaver/weaver.file.FileDownload?fileid=290754')` → 200 + `text/html`，235B 登录跳转脚本；
- 登录页字段：登录名/手机号码 + 登录密码 + **验证码**（人工登录不可省略）。

## 5. 待登录后补全/验证

- [ ] 人工登录后验证 `document.cookie`/`localStorage.access_token` 的实际形态；
- [ ] 若 `access_token` 可用于 FileDownload，即可免 Cookie 批量下载；
- [ ] 登录后按上述 `download` 逻辑（GET FileDownload?fileid=N）批量并发落盘附件；
- [ ] 抓取登录后才加载的 workflow/form 模块 bundle，补充表单附件下载的 URL 构造细节。
