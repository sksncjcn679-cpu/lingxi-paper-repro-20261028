"""Package only already QA'd figures/data; old assets copied with exact hash checks."""
from pathlib import Path
import sys
sys.path.insert(0, '/figure-code')
from package_completion_figures import package

package(Path('/rendered/results/data/figure_data.json'), Path('/rendered/results/render'),
        Path('/qa/visual_qa_single.json'), Path('/qa/visual_qa_double.json'),
        Path('/output/stage'), Path('/output/论文实验中文图表_保留原图新增四图_20261021.zip'))
