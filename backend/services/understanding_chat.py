"""Explicit opt-in to a check of an immutable artifact, never inferred from study time."""
import re

from services.artifact_store import ArtifactError
from services.artifact_selection import bind_selection, revision_selection
from services.conversation_context import resolve_reference


def is_check_request(message: str) -> bool:
    # Quoted examples and code are data, not requests to start a test.
    text = re.sub(r"```[\s\S]*?```|`[^`]*`|“[^”]*”|‘[^’]*’|\"[^\"]*\"|'[^']*'", "", str(message or ""))
    if re.search(r"(?:不要|不用|不必|别|暂不|先不).{0,8}(?:考|出题|测试|检查|测验)|(?:如果|假如|假设|比如).{0,12}(?:考考|出题|检查)", text):
        return False
    if re.search(r"(?:这句话|这个功能|这个说法).{0,10}(?:怎么|如何|是什么|什么意思|能否)", text):
        return False
    prefix = r"^\s*(?:(?:那就|现在|接下来|再|请|请你|麻烦你?|能不能|可以|帮我|给我|我想|我想要|我想让你)\s*)*"
    return bool(re.search(prefix + r"(?:考考我|考一下我|测测我|测试一下我|检查(?:一下)?我对.+(?:理解|掌握)|(?:基于|根据|围绕|针对).{0,70}(?:出.{0,6}题|考考我|理解检查)|(?:做|来|进行)(?:一次|个)?(?:当前作品|这一页|这页|当前页|所选段落)?理解检查)", text))


def resolve_check_source(student_id, user_id, message, resource_ref):
    artifact = resolve_reference(student_id, user_id, message, resource_ref)
    if artifact is None:
        raise ArtifactError(400, "请先在右侧选择要检查的作品；检查某一页时，请先翻到该页。")
    try:
        artifact = bind_selection(artifact, resource_ref, message, read_only=False)
        if artifact["type"] == "ppt":
            selection = revision_selection(artifact, message, artifact.get("selection"))
            artifact = {**artifact, "selection": selection}
        elif re.search(r"这(?:一)?页|当前页|第\s*[\d零一二三四五六七八九十]+\s*页", message):
            raise ArtifactError(400, "当前作品没有可定位的课件页面，请选择 PPT 页面，或改为检查当前作品／所选段落。")
    except ArtifactError as exc:
        detail = exc.detail.replace("单页修改", "单页检查").replace("修改的页码", "检查的页码").replace("只修改第三页", "考考我第三页").replace("这一页的修改", "这一页的检查")
        raise ArtifactError(exc.status_code, detail) from exc
    return artifact
