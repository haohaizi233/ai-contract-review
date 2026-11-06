import os

ref_files = os.listdir('参考')
with open('data/contract_merged/ref_files_list.txt', 'w', encoding='utf-8') as f:
    for idx, name in enumerate(ref_files, 1):
        f.write(f"{idx}. {name}\n")
print(f"Written {len(ref_files)} files.")
