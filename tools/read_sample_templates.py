import zipfile, os
import xml.etree.ElementTree as ET

def get_text(p):
    with zipfile.ZipFile(p) as z:
        tree = ET.fromstring(z.read('word/document.xml'))
        return ''.join(elem.text for elem in tree.iter() if elem.tag.endswith('}t'))

for fn in ['国内专利服务合同（申请）-恒程创新代理有限公司.docx', 
           '国内商标服务合同（注册）-恒程创新代理有限公司.docx',
           '国内专利服务合同（专利稳定性分析报告）-恒程创新代理有限公司 .docx']:
    full_p = os.path.join('参考', fn)
    if os.path.exists(full_p):
        txt = get_text(full_p)
        print(f"=== {fn} (len={len(txt)}) ===")
        print(txt[:300])
        print('\n')
