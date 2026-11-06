"""Exact, version-bound source ranges. Offsets are Unicode code points."""
from pydantic import BaseModel, Field, ConfigDict

from services.artifact_store import ArtifactError


TEXT_ARTIFACT_TYPES = {"document", "lecture", "lab", "assessment"}


class ArtifactSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    text: str = Field(min_length=1, strict=True)
    slide_number: int | None = Field(default=None, gt=0, strict=True)


def validate_selection(artifact, selection):
    if selection is None:
        return None
    if artifact["type"] not in TEXT_ARTIFACT_TYPES | {"ppt"}:
        raise ArtifactError(400, "此类作品暂不支持正文段落引用，请使用整篇引用。")
    if not isinstance(selection, dict):
        raise ArtifactError(400, "所选段落格式无效，请重新选择。")
    start, end, text = (selection.get(key) for key in ("start", "end", "text"))
    content = artifact["content"]
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(content)
            or not isinstance(text, str) or not text.strip()):
        raise ArtifactError(400, "所选段落范围无效，请重新选择。")
    if content[start:end] != text:
        raise ArtifactError(409, "所选段落与此版本原文不一致，请重新选择；本次没有修改作品。")
    result = {"start": start, "end": end, "text": text}
    number = selection.get("slide_number")
    if artifact["type"] == "ppt":
        from services.ppt_slides import list_slides
        if type(number) is not int or number < 1:
            raise ArtifactError(400, "PPT 局部引用必须选择完整的一页。")
        try:
            slides = list_slides(content)
        except ValueError as exc:
            raise ArtifactError(400, str(exc)) from exc
        result["slide_number"] = number
        if number > len(slides) or result != slides[number - 1]["selection"]:
            raise ArtifactError(409, "所选页面与此版本原文不一致，请重新选择。")
    elif number is not None:
        raise ArtifactError(400, "只有 PPT 课件可以引用页面。")
    return result


def revision_selection(artifact, instruction, selection=None):
    selection = validate_selection(artifact, selection)
    if artifact["type"] != "ppt":
        return selection
    from services.ppt_slides import list_slides, requested_slide
    try:
        number = requested_slide(instruction, selection.get("slide_number") if selection else None)
        if number is None:
            return None
        slides = list_slides(artifact["content"])
        if number < 1 or number > len(slides):
            raise ValueError(f"当前版本共 {len(slides)} 页，没有第 {number} 页。")
        return slides[number - 1]["selection"]
    except ValueError as exc:
        raise ArtifactError(400, str(exc)) from exc


def bind_selection(artifact, resource_ref, instruction="", read_only=False):
    """Attach a checked transient selection; never search for similar text."""
    selection = (resource_ref or {}).get("selection")
    if selection is None:
        return artifact
    if resource_ref.get("selection_source") == "preview":
        # Validate the original immutable snapshot before using even its page number.
        from services.artifact_store import read_artifact
        from services.ppt_slides import list_slides, requested_slide, PAGE, WHOLE
        original = read_artifact(resource_ref["resource_id"], resource_ref["version"],
                                 student_id=artifact.get("student_id") if artifact else None)
        checked = validate_selection(original, selection)
        if original["type"] != "ppt":
            raise ArtifactError(400, "预览页面引用只支持 PPT 课件。")
        if artifact is None or artifact["id"] != original["id"]:
            return artifact
        try:
            # Explicit current-message scope wins over the preview default.
            explicit = bool(PAGE.search(instruction) or WHOLE.search(instruction))
            number = requested_slide(instruction, None if explicit else checked["slide_number"])
            if number is None:
                return artifact
            slides = list_slides(artifact["content"])
            if number < 1 or number > len(slides):
                raise ValueError(f"当前版本共 {len(slides)} 页，没有第 {number} 页。")
            return {**artifact, "selection": slides[number - 1]["selection"]}
        except ValueError as exc:
            if read_only:
                return artifact
            raise ArtifactError(400, str(exc)) from exc
    if (artifact is None or artifact["id"] != resource_ref["resource_id"]
            or artifact["version"] != resource_ref["version"]):
        raise ArtifactError(409, "所选段落属于另一作品或版本，请取消段落引用或重新选择。")
    return {**artifact, "selection": validate_selection(artifact, selection)}
