from pathlib import Path
from docx import Document as DocxDocument
from pptx import Presentation


def load_docx(file_path: str) -> list[dict]:
    doc = DocxDocument(file_path)
    paragraphs = []
    current_heading = ""
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if para.style.name.startswith("Heading"):
            current_heading = text
        paragraphs.append({
            "text": text,
            "heading": current_heading,
            "style": para.style.name,
        })
    return paragraphs


def load_pptx(file_path: str) -> list[dict]:
    prs = Presentation(file_path)
    slides = []
    for i, slide in enumerate(prs.slides, 1):
        title = ""
        body_parts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if not text:
                        continue
                    if shape == slide.shapes[0] and not title:
                        title = text
                    else:
                        body_parts.append(text)
        if title or body_parts:
            slides.append({
                "slide_number": i,
                "title": title,
                "text": "\n".join(body_parts),
            })
    return slides


def load_base_data(base_dir: str) -> list[dict]:
    base = Path(base_dir)
    documents = []

    for file in sorted(base.glob("ppt/*.pptx")):
        slides = load_pptx(str(file))
        for s in slides:
            documents.append({
                "source_file": file.name,
                "resource_type": "lecture_slide",
                "chapter": s.get("title", ""),
                "slide_number": s["slide_number"],
                "text": f"{s['title']}\n{s['text']}" if s['title'] else s['text'],
            })

    for file in sorted(base.glob("lab/*.docx")):
        paragraphs = load_docx(str(file))
        for p in paragraphs:
            documents.append({
                "source_file": file.name,
                "resource_type": "lab",
                "chapter": p["heading"],
                "text": p["text"],
            })

    return documents
