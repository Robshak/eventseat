import os
import shutil
import sys
from pathlib import Path
from uuid import uuid4


def data_dir() -> Path:
    override = os.environ.get("EVENTSEAT_DATA_DIR")
    base = (
        Path(override)
        if override
        else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EventSeat"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def asset_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "assets" / name


def import_cover(source: str) -> str:
    from eventseat.domain import AppError

    path = Path(source)
    if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise AppError("Выберите изображение PNG, JPEG или WebP.")
    if path.stat().st_size > 15 * 1024 * 1024:
        raise AppError("Размер обложки должен быть не больше 15 МБ.")
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise AppError("Не удалось прочитать изображение. Выберите другой файл.") from exc
    folder = data_dir() / "covers"
    folder.mkdir(exist_ok=True)
    target = folder / f"{uuid4().hex}{path.suffix.lower()}"
    shutil.copyfile(path, target)
    return str(target)
