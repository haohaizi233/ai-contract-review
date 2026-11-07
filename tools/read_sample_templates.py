import zipfile, os
import xml.etree.ElementTree as ET

def get_text(p):
    with zipfile.ZipFile(p) as z:
        tree = ET.fromstring(z.read('word/document.xml'))
        return ''.join(elem.text for elem in tree.iter() if elem.tag.endswith('}t'))

ref_dir = os.environ.get("CONTRACT_KB_TEMPLATE_DIR", "参考")
sample_files = sorted(f for f in os.listdir(ref_dir) if f.endswith(".docx"))[:3] if os.path.isdir(ref_dir) else []
for fn in sample_files:
    full_p = os.path.join(ref_dir, fn)
    if os.path.exists(full_p):
        txt = get_text(full_p)
        print(f"=== {fn} (len={len(txt)}) ===")
        print(txt[:300])
        print('\n')
