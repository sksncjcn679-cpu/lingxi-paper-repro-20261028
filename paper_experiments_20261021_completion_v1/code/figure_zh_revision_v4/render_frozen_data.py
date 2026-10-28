"""Render only the existing exact frozen data projection with Chinese labels."""
from pathlib import Path
import sys
sys.path.insert(0,'/figure-code')
from render_completion_figures import render
render(Path('/frozen-data/figure_data.json'),Path('/output/render'))
