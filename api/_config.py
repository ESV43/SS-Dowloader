from pathlib import Path
import tempfile

WORK_DIR = Path(tempfile.gettempdir()) / "slideswift"

PDF_WIDTH = 1440
PDF_QUALITY = 78
PPTX_WIDTH = 1440
PPTX_QUALITY = 78

PDF_LAYOUT = (960, 540)
SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
}
