from pathlib import Path

OUT=Path(__file__).resolve().parent
OLD=OUT.parent/'figure1_framework'
s=(OLD/'render_framework.cjs').read_text(encoding='utf-8')
for before,after in [
 ('W=1600,H=900','W=1680,H=945'),
 ("getCell('role_label_plan')","getCell('up_task_title')"),
 ("'1600'","'1680'"),("'900'","'945'"),
 ('0 0 1600 900','0 0 1680 945'),
 ('width:1600px;height:900px','width:1680px;height:945px'),
 ('build_framework.py','build_reference_framework.py')]:s=s.replace(before,after)
(OUT/'render_framework.cjs').write_text(s,encoding='utf-8')
f=(OLD/'finalize_framework.py').read_text(encoding='utf-8').replace('build_framework.py','build_reference_framework.py')
(OUT/'finalize_framework.py').write_text(f,encoding='utf-8')
print('Reference-style rendering and verification helpers prepared.')
