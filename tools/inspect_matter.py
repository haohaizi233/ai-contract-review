import glob

files = glob.glob('data/contract_merged/cases/*/*.md')
count = 0
with open('data/contract_merged/matter_samples.txt', 'w', encoding='utf-8') as out:
    for f in files:
        with open(f, 'r', encoding='utf-8') as fp:
            text = fp.read()
        if 'contract_apply_matter' in text:
            # extract the table under contract_apply_matter
            lines = text.splitlines()
            for idx, l in enumerate(lines):
                if 'contract_apply_matter' in l:
                    sub = lines[idx:idx+15]
                    out.write(f"File: {f}\n")
                    out.write('\n'.join(sub) + '\n\n')
                    count += 1
                    break
        if count >= 5:
            break
print("Done dumping matter samples.")
