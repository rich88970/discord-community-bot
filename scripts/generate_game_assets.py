from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import game_images  # noqa: E402


if __name__ == "__main__":
    game_images.generate_assets()
    print(f"Generated game assets in {game_images.ASSET_DIR}")

