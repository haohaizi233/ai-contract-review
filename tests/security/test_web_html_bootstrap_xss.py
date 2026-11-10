# -*- coding: utf-8 -*-
"""Phase 14-R G7/DEFECT-P1-04: Web 工作台 HTML 初始数据注入与 XSS 防护回归测试。"""

from __future__ import annotations


from tools.contract_diff_web import generate_rendered_html


def test_reproduce_defect_html_script_tag_injection():
    """复现缺陷：未转义的 </script> 标签注入会闭合 script 上下文，构成 XSS 漏洞！"""
    # 当 diff_data 或任何合同元数据包含 </script><script>alert('xss')</script>
    xss_payload = "</script><script>alert('xss')</script>"
    # 我们测试当 HTML 中生成数据时，绝不能出现裸露闭合标签
    html = generate_rendered_html(initial_diff_data={"malicious": xss_payload})

    # 在安全修复后，</script> 必须被安全转义为 <\/script> 或 \u003c/script\u003e
    # 绝不能存在未转义的裸露闭合 </script>
    # 检查 window.__INITIAL_DATA__ 区域
    assert "</script><script>alert('xss')</script>" not in html, (
        "缺陷证实：数据中包含的 </script> 未被转义，直接闭合了 script 标签造成 XSS 注入！"
    )
