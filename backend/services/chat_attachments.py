import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from docx import Document as DocxDocument
from pptx import Presentation
from pypdf import PdfReader

from config import BASE_DIR


ATTACHMENT_DIR = Path(BASE_DIR) / "data" / "chat_attachments"
PADDLEX_CACHE_DIR = Path(BASE_DIR) / "data" / "paddlex_cache"
OCR_RUNTIME_HOME = PADDLEX_CACHE_DIR / "home"
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(PADDLEX_CACHE_DIR))
os.environ.setdefault("PADDLE_HOME", str(PADDLEX_CACHE_DIR / "paddle"))
os.environ.setdefault("XDG_CACHE_HOME", str(PADDLEX_CACHE_DIR / "runtime"))
os.environ.setdefault("HF_HOME", str(PADDLEX_CACHE_DIR / "huggingface"))
os.environ.setdefault("MODELSCOPE_CACHE", str(PADDLEX_CACHE_DIR / "modelscope"))
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".html", ".htm",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".css", ".scss", ".sql", ".yaml", ".yml",
}
DOCUMENT_EXTENSIONS = {".docx", ".ppt", ".pptx", ".pdf"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS
MAX_ATTACHMENT_MB = max(1, int(os.getenv("NETMIND_MAX_ATTACHMENT_MB", "256")))
MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024
PDF_OCR_PAGE_LIMIT = max(1, int(os.getenv("NETMIND_PDF_OCR_PAGE_LIMIT", "16")))

_ocr = None
_ocr_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(filename: str) -> str:
    return Path(filename or "attachment").name.replace("\x00", "").strip() or "attachment"


def _get_ocr():
    global _ocr
    if _ocr is not None:
        return _ocr
    with _ocr_lock:
        if _ocr is not None:
            return _ocr
        try:
            OCR_RUNTIME_HOME.mkdir(parents=True, exist_ok=True)
            original_home = os.environ.get("HOME")
            original_user_profile = os.environ.get("USERPROFILE")
            os.environ["HOME"] = str(OCR_RUNTIME_HOME)
            os.environ["USERPROFILE"] = str(OCR_RUNTIME_HOME)
            try:
                from paddleocr import PaddleOCR
                try:
                    _ocr = PaddleOCR(
                        text_detection_model_name="PP-OCRv5_mobile_det",
                        text_recognition_model_name="PP-OCRv5_mobile_rec",
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                        device="cpu",
                        enable_mkldnn=False,
                        engine="paddle",
                    )
                except TypeError:
                    # PaddleOCR 3.0-3.4 does not expose the engine option.
                    _ocr = PaddleOCR(
                        text_detection_model_name="PP-OCRv5_mobile_det",
                        text_recognition_model_name="PP-OCRv5_mobile_rec",
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                        device="cpu",
                        enable_mkldnn=False,
                    )
            finally:
                if original_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = original_home
                if original_user_profile is None:
                    os.environ.pop("USERPROFILE", None)
                else:
                    os.environ["USERPROFILE"] = original_user_profile
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR is not installed. Install paddlepaddle>=3.0 and paddleocr>=3.0 first."
            ) from exc
    return _ocr


def _as_plain_result(result) -> dict:
    if isinstance(result, dict):
        return result
    for attr in ("json", "res"):
        value = getattr(result, attr, None)
        if callable(value):
            value = value()
        if isinstance(value, dict):
            return value
    try:
        return dict(result)
    except (TypeError, ValueError):
        return {}


def _find_recognized_text(value) -> list[str]:
    if isinstance(value, dict):
        texts = value.get("rec_texts")
        if isinstance(texts, (list, tuple)):
            return [str(item).strip() for item in texts if str(item).strip()]
        single = value.get("rec_text")
        if str(single or "").strip():
            return [str(single).strip()]
        found = []
        for nested in value.values():
            found.extend(_find_recognized_text(nested))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for nested in value:
            found.extend(_find_recognized_text(nested))
        return found
    return []


def _read_with_ocr(path: Path) -> tuple[str, dict]:
    pages = []
    for page_index, result in enumerate(_get_ocr().predict(str(path)), 1):
        payload = _as_plain_result(result)
        lines = _find_recognized_text(payload)
        if lines:
            pages.append(f"[Page {page_index}]\n" + "\n".join(lines))
    text = "\n\n".join(pages).strip()
    return text, {
        "parser": "paddleocr",
        "pages": len(pages),
        "ocr_model": "PP-OCRv5",
    }


def _read_pdf(path: Path) -> tuple[str, dict]:
    reader = PdfReader(str(path))
    pages = []
    for page_index, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[Page {page_index}]\n{text}")

    extracted = "\n\n".join(pages).strip()
    page_count = len(reader.pages)
    minimum_text = max(160, page_count * 24)
    if len(extracted) >= minimum_text:
        return extracted, {
            "parser": "pypdf",
            "pages": page_count,
            "text_pages": len(pages),
        }

    import fitz

    selected_pages = list(range(min(page_count, PDF_OCR_PAGE_LIMIT)))
    if page_count > PDF_OCR_PAGE_LIMIT:
        head_count = min(8, PDF_OCR_PAGE_LIMIT)
        remaining_slots = PDF_OCR_PAGE_LIMIT - head_count
        selected_pages = list(range(head_count))
        if remaining_slots > 0:
            span = max(1, page_count - head_count)
            selected_pages.extend(
                min(page_count - 1, head_count + round(span * index / remaining_slots) - 1)
                for index in range(1, remaining_slots + 1)
            )
        selected_pages = list(dict.fromkeys(selected_pages))

    ocr_pages = []
    document = fitz.open(str(path))
    try:
        with tempfile.TemporaryDirectory(prefix="netmind-pdf-ocr-") as temp_dir:
            temp_root = Path(temp_dir)
            for page_index in selected_pages:
                page = document.load_page(page_index)
                image_path = temp_root / f"page-{page_index + 1}.png"
                page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False).save(str(image_path))
                page_text, _ = _read_with_ocr(image_path)
                cleaned = re.sub(r"^\[Page 1\]\s*", "", page_text.strip())
                if cleaned:
                    ocr_pages.append(f"[Page {page_index + 1}]\n{cleaned}")
    finally:
        document.close()

    partial = page_count > len(selected_pages)
    return "\n\n".join(ocr_pages), {
        "parser": "pypdf -> paddleocr",
        "pages": page_count,
        "ocr_pages": [index + 1 for index in selected_pages],
        "partial": partial,
        "warning": (
            f"该 PDF 没有可用文本层。为避免长时间阻塞，已抽取 {len(selected_pages)}/{page_count} 页进行 OCR。"
            if partial
            else ""
        ),
        "text_layer_characters": len(extracted),
        "ocr_model": "PP-OCRv5",
    }


def _read_docx(path: Path) -> tuple[str, dict]:
    doc = DocxDocument(str(path))
    paragraphs = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                paragraphs.append(" | ".join(cells))
    return "\n".join(paragraphs), {"parser": "python-docx", "paragraphs": len(paragraphs)}


def _read_pptx(path: Path) -> tuple[str, dict]:
    presentation = Presentation(str(path))
    slides = []
    for index, slide in enumerate(presentation.slides, 1):
        lines = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                lines.extend(
                    paragraph.text.strip()
                    for paragraph in shape.text_frame.paragraphs
                    if paragraph.text.strip()
                )
        if lines:
            slides.append(f"[Slide {index}]\n" + "\n".join(lines))
    return "\n\n".join(slides), {"parser": "python-pptx", "slides": len(slides)}


def _convert_ppt_with_powerpoint(path: Path, output_dir: Path) -> Path:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = None
    presentation = None
    target = output_dir / f"{path.stem}.pptx"
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        app.DisplayAlerts = 1
        presentation = app.Presentations.Open(
            str(path.resolve()),
            ReadOnly=True,
            Untitled=False,
            WithWindow=False,
        )
        presentation.SaveAs(str(target.resolve()), 24)
        return target
    finally:
        if presentation is not None:
            presentation.Close()
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


def _find_soffice() -> str | None:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if executable:
        return executable
    for candidate in (
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _convert_ppt_with_libreoffice(path: Path, output_dir: Path) -> Path:
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice is not installed")
    result = subprocess.run(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pptx",
            "--outdir",
            str(output_dir),
            str(path.resolve()),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    target = output_dir / f"{path.stem}.pptx"
    if result.returncode != 0 or not target.is_file():
        detail = (result.stderr or result.stdout or "conversion failed").strip()
        raise RuntimeError(detail)
    return target


def _ppt_record_text(stream: bytes) -> list[str]:
    text_runs = []

    def parse_records(start: int, end: int, depth: int = 0):
        if depth > 48:
            return
        offset = start
        while offset + 8 <= end:
            ver_instance, record_type, record_length = struct.unpack_from("<HHI", stream, offset)
            payload_start = offset + 8
            payload_end = payload_start + record_length
            if payload_end > end or payload_end < payload_start:
                return
            record_version = ver_instance & 0x000F
            payload = stream[payload_start:payload_end]
            if record_type == 4000:
                text_runs.append(payload.decode("utf-16-le", errors="ignore"))
            elif record_type == 4008:
                text_runs.append(payload.decode("cp1252", errors="ignore"))
            elif record_version == 0x000F and record_length >= 8:
                parse_records(payload_start, payload_end, depth + 1)
            offset = payload_end

    parse_records(0, len(stream))
    return text_runs


def _read_ppt_ole(path: Path) -> tuple[str, dict]:
    try:
        import olefile
    except ImportError as exc:
        raise RuntimeError("olefile is not installed") from exc

    if not olefile.isOleFile(str(path)):
        raise RuntimeError("file is not a valid legacy PowerPoint document")
    with olefile.OleFileIO(str(path)) as document:
        stream_name = "PowerPoint Document"
        if not document.exists(stream_name):
            raise RuntimeError("PowerPoint Document stream is missing")
        stream = document.openstream(stream_name).read()

    lines = []
    seen = set()
    for run in _ppt_record_text(stream):
        for line in run.replace("\x00", "").replace("\x0b", "\n").splitlines():
            normalized = " ".join(line.split()).strip()
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            lines.append(normalized)
    return "\n".join(lines), {
        "parser": "ole-ppt-text",
        "text_runs": len(lines),
    }


def _read_ppt(path: Path) -> tuple[str, dict]:
    errors = []
    signature = path.read_bytes()[:8]
    if signature.startswith(b"PK\x03\x04"):
        text, metadata = _read_pptx(path)
        return text, {
            **metadata,
            "parser": "python-pptx (OOXML file with .ppt extension)",
            "detected_format": "pptx",
        }

    try:
        text, metadata = _read_ppt_ole(path)
        if text.strip():
            return text, {
                **metadata,
                "detected_format": "ppt",
            }
    except Exception as exc:
        errors.append(f"ole-ppt-text: {exc}")

    with tempfile.TemporaryDirectory(prefix="netmind-ppt-") as temp_dir:
        output_dir = Path(temp_dir)
        converters = []
        if os.name == "nt":
            converters.append(("powerpoint-com", _convert_ppt_with_powerpoint))
        converters.append(("libreoffice", _convert_ppt_with_libreoffice))
        for converter_name, converter in converters:
            try:
                converted = converter(path, output_dir)
                text, metadata = _read_pptx(converted)
                return text, {
                    **metadata,
                    "parser": f"{converter_name} -> {metadata['parser']}",
                    "converted_from": "ppt",
                }
            except Exception as exc:
                errors.append(f"{converter_name}: {exc}")

    raise RuntimeError(
        "旧版 PPT 解析失败。文件可能已损坏、加密或不是真正的 .ppt 文件。"
        f" 解析详情：{' | '.join(errors)}"
    )


def _extract_text(path: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8", errors="ignore"), {"parser": "plain-text"}
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".ppt":
        return _read_ppt(path)
    if suffix == ".pptx":
        return _read_pptx(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix in IMAGE_EXTENSIONS:
        return _read_with_ocr(path)
    raise ValueError(f"Unsupported attachment type: {suffix or 'unknown'}")


def create_attachment(filename: str, content: bytes) -> dict:
    name = _safe_name(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported attachment type: {suffix or 'unknown'}")
    if len(content) > MAX_ATTACHMENT_BYTES:
        size_mb = len(content) / (1024 * 1024)
        raise ValueError(
            f"文件大小为 {size_mb:.1f} MB，超过当前 {MAX_ATTACHMENT_MB} MB 的单文件上限。"
        )

    attachment_id = uuid.uuid4().hex
    item_dir = ATTACHMENT_DIR / attachment_id
    item_dir.mkdir(parents=True, exist_ok=True)
    source_path = item_dir / name
    source_path.write_bytes(content)

    metadata = {
        "id": attachment_id,
        "name": name,
        "extension": suffix,
        "size_bytes": len(content),
        "status": "processing",
        "created_at": _now_iso(),
    }
    try:
        text, parser_metadata = _extract_text(source_path)
        text = text.strip()
        metadata.update({
            "status": "ready" if text else "empty",
            "text": text,
            "characters": len(text),
            "preview": text[:240],
            **parser_metadata,
        })
    except Exception as exc:
        metadata.update({
            "status": "error",
            "text": "",
            "characters": 0,
            "preview": "",
            "error": str(exc),
        })

    (item_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return public_attachment(metadata)


def _load_attachment(attachment_id: str) -> dict | None:
    safe_id = Path(str(attachment_id or "")).name
    metadata_path = ATTACHMENT_DIR / safe_id / "metadata.json"
    if not metadata_path.is_file():
        return None
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def public_attachment(metadata: dict) -> dict:
    return {key: value for key, value in metadata.items() if key != "text"}


def get_attachment_context(attachment_ids: list[str] | None) -> dict:
    items = []
    sections = []
    for attachment_id in attachment_ids or []:
        metadata = _load_attachment(attachment_id)
        if not metadata:
            continue
        items.append(public_attachment(metadata))
        text = str(metadata.get("text") or "").strip()
        if text:
            sections.append(f"## Attachment: {metadata.get('name', attachment_id)}\n\n{text}")
    return {
        "items": items,
        "text": "\n\n".join(sections),
        "names": [item.get("name", "") for item in items],
    }


def delete_attachment(attachment_id: str) -> bool:
    safe_id = Path(str(attachment_id or "")).name
    item_dir = ATTACHMENT_DIR / safe_id
    if not item_dir.is_dir():
        return False
    for path in item_dir.iterdir():
        if path.is_file():
            path.unlink()
    item_dir.rmdir()
    return True
