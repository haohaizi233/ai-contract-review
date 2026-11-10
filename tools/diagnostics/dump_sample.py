with open(
    "data/contract_merged/cases/CIP/CIP2020HT02025-1_2d3fc66d48.md",
    "r",
    encoding="utf-8",
) as f:
    text = f.read()

with open("data/contract_merged/sample_case_full.md", "w", encoding="utf-8") as f:
    f.write(text)

print(f"Sample length: {len(text)}")
