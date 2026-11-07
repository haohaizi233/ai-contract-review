import zipfile
import xml.etree.ElementTree as ET


def extract_docx_text(docx_path):
    with zipfile.ZipFile(docx_path) as z:
        xml_content = z.read("word/document.xml")
        tree = ET.fromstring(xml_content)
        texts = []
        for elem in tree.iter():
            if elem.tag.endswith("}t"):
                if elem.text:
                    texts.append(elem.text)
            elif elem.tag.endswith("}p"):
                texts.append("\n")
        return "".join(texts)


text = extract_docx_text("参考/合同编号管理制度20260108.docx")
with open(
    "data/contract_merged/contract_numbering_rules.txt", "w", encoding="utf-8"
) as f:
    f.write(text)

print(f"Extracted {len(text)} characters from 合同编号管理制度20260108.docx")
