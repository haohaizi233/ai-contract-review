import os, re
import zipfile
import xml.etree.ElementTree as ET

def get_text(p):
    try:
        with zipfile.ZipFile(p) as z:
            tree = ET.fromstring(z.read('word/document.xml'))
            return ''.join(elem.text for elem in tree.iter() if elem.tag.endswith('}t'))
    except Exception as e:
        return str(e)

docs = [f for f in os.listdir('参考') if f.endswith('.docx')]
docs.sort()

with open('data/contract_merged/template_analysis.txt', 'w', encoding='utf-8') as out:
    out.write(f"Total Templates: {len(docs)}\n\n")
    for d in docs:
        p = os.path.join('参考', d)
        txt = get_text(p)
        out.write(f"====================================================\n")
        out.write(f"模板文件名: {d}\n")
        out.write(f"文本总字数: {len(txt)}\n")
        # Extract title or first few lines
        first_part = txt[:400].replace('\n', ' ')
        out.write(f"开头摘要: {first_part}\n\n")

print("Analysis written to data/contract_merged/template_analysis.txt")
