"""Create a tiny synthetic ZIP for trying the complete Folio workflow."""
import argparse
from pathlib import Path
import zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', nargs='?', default='data/samples/folio-sample.zip')
args = parser.parse_args()
target = Path(args.output)
target.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('project/brief.txt', 'Folio sample project\n\nThe project budget is 120 units.\nThe deadline is Friday.\nThe owner is the sample team.\n')
    archive.writestr('project/decisions.md', '# Sample decisions\n\n- The first milestone is a working prototype.\n- Track the budget in the project brief.\n- Review progress with the sample team on Friday.\n')
print(f'Created synthetic sample: {target}')
