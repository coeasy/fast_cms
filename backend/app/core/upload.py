"""上传内容校验。

上传接口最容易被绕过的往往不是大小限制，而是「改名」：把 ``.exe`` / ``.html``
/ ``.svg`` 改成 ``.pdf`` 传上来，再诱导别人下载后本地打开。因此这里同时看三件事：

1. **扩展名必须在白名单里**——不在白名单的一律拒绝，没有「先存下来再说」；
2. **服务端嗅探到的真实类型必须和扩展名自洽**——客户端声明的
   ``Content-Type`` 和文件名一样不可信，只有 magic number 可信；
3. **下载必须带 ``nosniff``**——否则浏览器会自己猜类型，把 ``text/plain``
   的 HTML 当页面渲染（见 ``app.modules.content.router``）。

规则刻意做得不对称：**图片扩展名要求「嗅探必须成功」，其他格式只要求「嗅探结果不能矛盾」**。
原因是 ``filetype`` 只认二进制签名，HTML/SVG/JS 这类能执行脚本的格式它一律返回
``None``——如果对所有格式都「嗅探不到就放过」，把 HTML 改名成 ``.png`` 就能混进来，
而图片恰恰是唯一会被内联展示的类型。反过来，一个带 BOM 的 PDF 被误杀的代价只是
「当成二进制下载」，不影响打开，所以不需要索证。

纯文本格式（txt/csv/md/json/xml/log）没有 magic number，单独列一类：
它们允许「嗅探不到」，但反过来「嗅探到了二进制签名」同样要拒绝——
一个自称 ``.txt`` 却带着 PNG 签名的文件，多半是在夹带。
"""

from __future__ import annotations

from app.core.errors import AppError

# 可在管理后台上传的扩展名白名单。
# 故意不含：可执行脚本与页面（.exe/.sh/.bat/.html/.htm/.svg/.js/.php/.jsp），
# 也不含符号链接类扩展名。
UPLOAD_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        # 文档
        ".pdf", ".txt", ".md", ".csv", ".json", ".xml", ".log",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        # 压缩包
        ".zip", ".gz", ".tar", ".7z", ".rar",
        # 图片
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
        # 音视频
        ".mp3", ".mp4", ".wav", ".webm",
    }
)

# 没有 magic number 的纯文本格式：只靠扩展名兜底。
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".csv", ".json", ".xml", ".log"}
)

# 会被内联展示的图片扩展名。这类文件一旦混进 HTML，就是存储型 XSS，
# 所以不能只靠「扩展名在白名单里」——必须真的嗅到图片签名。
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}
)

# 纯文本扩展名对应的 Content-Type，用于客户端没声明、服务端也嗅探不到时兜底。
TEXT_EXTENSION_MIMES: dict[str, str] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".log": "text/plain",
}

# 二进制扩展名 → 可接受的真实类型。filetype 对 zip/7z/rar 的判定与
# Python mimetypes 不完全一致，所以这里用「集合」而不是「一个值」。
EXTENSION_MIMES: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"application/pdf"}),
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".gif": frozenset({"image/gif"}),
    ".webp": frozenset({"image/webp"}),
    ".bmp": frozenset({"image/bmp"}),
    ".ico": frozenset({"image/x-icon", "image/vnd.microsoft.icon"}),
    ".zip": frozenset({"application/zip"}),
    ".gz": frozenset({"application/gzip"}),
    ".tar": frozenset({"application/x-tar"}),
    ".7z": frozenset({"application/x-7z-compressed"}),
    ".rar": frozenset({"application/x-rar-compressed", "application/vnd.rar"}),
    ".mp3": frozenset({"audio/mpeg"}),
    ".mp4": frozenset({"video/mp4", "audio/mp4"}),
    ".wav": frozenset({"audio/x-wav", "audio/wav", "audio/vnd.wave"}),
    ".webm": frozenset({"video/webm", "audio/webm"}),
    ".doc": frozenset({"application/msword"}),
    ".docx": frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    ),
    ".xls": frozenset({"application/vnd.ms-excel"}),
    ".xlsx": frozenset(
        {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    ),
    ".ppt": frozenset({"application/vnd.ms-powerpoint"}),
    ".pptx": frozenset(
        {"application/vnd.openxmlformats-officedocument.presentationml.presentation"}
    ),
}


def normalize_extension(name: str | None) -> str:
    """取小写扩展名；取不到或形态不合法时返回空串。"""
    if not name:
        return ""
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in base:
        return ""
    suffix = base.rsplit(".", 1)[-1].strip().lower()
    if not suffix.isalnum() or not 1 <= len(suffix) <= 10:
        return ""
    return f".{suffix}"


def sniff_mime(header: bytes) -> str | None:
    """按 magic number 猜类型；认不出返回 ``None``（纯文本走这条）。"""
    import filetype

    if not header:
        return None
    guessed = filetype.guess(header)
    return guessed.mime if guessed else None


def resolve_mime(suffix: str, sniffed: str | None) -> str:
    """决定最终落库的 Content-Type。

    **完全不采用客户端声明的 ``Content-Type``**：它和文件名一样是攻击者可控的。
    一个自称 ``image/png`` 的 HTML 如果照单全收，``is_safe_image`` 就会把它
    当图片内联返回。嗅探不到、扩展名又没有映射时统一落到
    ``application/octet-stream``——宁可贵重文件被当成二进制，也不把类型决定权交出去。
    """
    if sniffed:
        return sniffed[:128]
    if suffix in TEXT_EXTENSION_MIMES:
        return TEXT_EXTENSION_MIMES[suffix]
    return "application/octet-stream"


def _check_signature(suffix: str, sniffed: str) -> None:
    """嗅探到的类型与扩展名映射矛盾时拒绝。

    没有映射的扩展名（理论上不会出现，白名单和映射表由测试保证对齐）
    直接放过，由下载端的 ``nosniff`` 兜底。
    """
    allowed = EXTENSION_MIMES.get(suffix)
    if allowed is not None and sniffed not in allowed:
        raise AppError(
            "TOOL_FILE_TYPE_MISMATCH",
            f"文件内容是 {sniffed}，与扩展名 {suffix} 不符",
            422,
        )


def validate_upload(
    name: str | None,
    header: bytes,
    *,
    image_only: bool = False,
) -> tuple[str, str]:
    """校验一次上传，返回 ``(扩展名, Content-Type)``；不合法直接抛 ``AppError``。

    ``image_only`` 走更严的图片白名单（头像等会内联展示的场景）。
    """
    suffix = normalize_extension(name)
    sniffed = sniff_mime(header)

    if image_only:
        # 头像一定会被内联展示，必须同时满足：扩展名是图片 + 真的嗅到图片签名
        if suffix not in IMAGE_EXTENSIONS or sniffed is None:
            raise AppError("TOOL_FILE_TYPE_FORBIDDEN", "头像只能是 PNG/JPEG/GIF/WebP/BMP 图片", 422)
        from app.modules.content.service import SAFE_IMAGE_TYPES

        if sniffed not in SAFE_IMAGE_TYPES:
            raise AppError("TOOL_FILE_TYPE_MISMATCH", "文件内容与图片格式不符", 422)
        return suffix, sniffed

    if not suffix:
        raise AppError("TOOL_FILE_NO_EXTENSION", "文件缺少扩展名，无法判定类型", 422)
    if suffix not in UPLOAD_ALLOWED_EXTENSIONS:
        raise AppError(
            "TOOL_FILE_TYPE_FORBIDDEN",
            f"不允许上传 {suffix} 类型的文件",
            422,
        )

    if suffix in IMAGE_EXTENSIONS:
        # filetype 认不出 HTML/SVG/JS（它们没有二进制签名），所以「嗅探失败」
        # 对图片扩展名不是「无法判断」，而是「大概率不是图片」。
        if sniffed is None:
            raise AppError(
                "TOOL_FILE_TYPE_MISMATCH",
                f"{suffix} 文件没有可识别的图片签名",
                422,
            )
        _check_signature(suffix, sniffed)
        return suffix, sniffed

    if sniffed is not None:
        if suffix in TEXT_EXTENSIONS:
            # 自称纯文本却带二进制签名，多半在夹带
            raise AppError(
                "TOOL_FILE_TYPE_MISMATCH",
                f"{suffix} 应为纯文本，实际内容是 {sniffed}",
                422,
            )
        _check_signature(suffix, sniffed)

    return suffix, resolve_mime(suffix, sniffed)
