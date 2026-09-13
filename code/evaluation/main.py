"""Re-run the exact same pipeline and regenerate evaluation reports."""
import subprocess,sys
from pathlib import Path
if __name__=='__main__':
    raise SystemExit(subprocess.call([sys.executable,str(Path(__file__).resolve().parents[1]/'main.py'),*sys.argv[1:]]))
