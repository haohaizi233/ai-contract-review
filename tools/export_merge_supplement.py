import sys,csv,shutil
from pathlib import Path
sys.path.insert(0,str(Path('tools').resolve()))
import tender_case_export as exporter
out=Path('data/contract_merge_supplement').resolve()
cache=out/'_cache';cache.mkdir(parents=True,exist_ok=True)
for name in ['oa.pkl','oa_resources.json']:
 src=Path(exporter.CACHE_DIR)/name
 if src.exists() and not (cache/name).exists():shutil.copy2(src,cache/name)
for name,value in {'OUT':out,'CASES_DIR':out/'cases','IMG_DIR':out/'images','CACHE_DIR':cache,'PROGRESS_PATH':out/'_progress.json','STATE_PATH':out/'_state.json'}.items():setattr(exporter,name,str(value))
sys.argv=['tender_case_export.py','--scope-index',str(Path('data/contract_merge_supplement_scope.csv').resolve()),'--no-fetch','--no-web']
exporter.main()
