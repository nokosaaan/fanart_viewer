"""Custom gallery-dl extractors packaged with fanart_viewer.

Provides helper utilities to locate extractor modules for gallery-dl's -X
argument and dynamically register all custom extractors in-process.
"""

import importlib
import logging
import os
import pkgutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def get_extractors_dir() -> str:
    """Return the absolute path to the extractors directory."""
    # 1. Development or standard package location
    p = Path(__file__).resolve().parent
    if p.is_dir() and any(p.glob("*.py")):
        return str(p)

    # 2. Frozen PyInstaller build location
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        for cand in [
            exe_dir / "item" / "extractors",
            exe_dir / "_internal" / "item" / "extractors",
            exe_dir / "backend" / "item" / "extractors",
            exe_dir / "_internal" / "backend" / "item" / "extractors",
        ]:
            if cand.is_dir():
                return str(cand)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            m_cand = Path(meipass) / "item" / "extractors"
            if m_cand.is_dir():
                return str(m_cand)

    return str(p)


def register_custom_extractors():
    """Dynamically register all Extractor subclasses found in this package into gallery_dl."""
    try:
        import gallery_dl.extractor
        from gallery_dl.extractor.common import Extractor
    except ImportError:
        return

    package_dir = Path(__file__).resolve().parent
    for _, module_name, is_pkg in pkgutil.iter_modules([str(package_dir)]):
        if is_pkg or module_name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{module_name}")
            for attr_name in dir(mod):
                cls = getattr(mod, attr_name)
                if (
                    isinstance(cls, type)
                    and issubclass(cls, Extractor)
                    and cls is not Extractor
                    and getattr(cls, "pattern", None)
                ):
                    gallery_dl.extractor.add(cls)
        except Exception as e:
            logger.warning("Failed to register custom extractor from %s: %s", module_name, e)
