from __future__ import annotations

import base64

import pytest

from app.core.errors import AppError
from app.core.upload import (
    EXTENSION_MIMES,
    IMAGE_EXTENSIONS,
    TEXT_EXTENSIONS,
    UPLOAD_ALLOWED_EXTENSIONS,
    normalize_extension,
    resolve_mime,
    validate_upload,
)

# 1x1 透明 PNG，filetype 靠前 8 字节的签名就能认出来
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8"
    "DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
HTML = b"<!DOCTYPE html><html><body><script>alert(1)</script></body></html>"
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"


@pytest.mark.parametrize(
    "name",
    ["a.exe", "a.html", "a.htm", "a.svg", "a.js", "a.php", "a.sh", "a.bat", "a.ps1", "a.jar"],
)
def test_rejects_dangerous_extensions(name: str) -> None:
    """可执行与可渲染类型一律拒绝：改名上传是最常见的绕过方式。"""
    with pytest.raises(AppError) as exc:
        validate_upload(name, b"whatever")
    assert exc.value.code == "TOOL_FILE_TYPE_FORBIDDEN"


def test_rejects_missing_extension() -> None:
    with pytest.raises(AppError) as exc:
        validate_upload("README", b"plain text")
    assert exc.value.code == "TOOL_FILE_NO_EXTENSION"


def test_accepts_plain_text_without_signature() -> None:
    """纯文本没有 magic number，filetype 认不出来，这时只能靠扩展名兜底。"""
    suffix, mime = validate_upload("notes.txt", b"hello fastcms")
    assert suffix == ".txt"
    assert mime == "text/plain"


def test_text_extension_with_binary_signature_is_rejected() -> None:
    """自称 .txt 却带着 PNG 签名，说明在夹带二进制内容。"""
    with pytest.raises(AppError) as exc:
        validate_upload("smuggled.txt", PNG)
    assert exc.value.code == "TOOL_FILE_TYPE_MISMATCH"


def test_image_extension_with_html_content_is_rejected() -> None:
    """.png 里其实是 HTML：如果内联展示就是存储型 XSS。

    filetype 认不出 HTML（它没有二进制签名），所以这条靠的是
    「图片扩展名必须嗅到图片签名」这条更严的规则。
    """
    with pytest.raises(AppError) as exc:
        validate_upload("x.png", HTML)
    assert exc.value.code == "TOOL_FILE_TYPE_MISMATCH"


def test_client_declared_content_type_is_never_trusted() -> None:
    """声明成 image/png 也不该让 HTML 变成图片——落库类型只由服务端决定。"""
    with pytest.raises(AppError):
        validate_upload("x.png", HTML)
    # 嗅探失败时统一落 octet-stream，绝不采用客户端声明
    assert resolve_mime(".zip", None) == "application/octet-stream"
    assert resolve_mime(".png", None) == "application/octet-stream"


def test_genuine_png_is_accepted() -> None:
    suffix, mime = validate_upload("logo.png", PNG)
    assert suffix == ".png"
    assert mime == "image/png"


def test_pdf_is_accepted_and_sniffed_type_wins() -> None:
    suffix, mime = validate_upload("doc.pdf", PDF)
    assert suffix == ".pdf"
    assert mime == "application/pdf"


def test_image_only_requires_real_image() -> None:
    with pytest.raises(AppError):
        validate_upload("avatar.txt", b"not an image", image_only=True)
    with pytest.raises(AppError):
        validate_upload("avatar.svg", b"<svg/>", image_only=True)
    with pytest.raises(AppError):
        validate_upload("avatar.png", HTML, image_only=True)
    with pytest.raises(AppError):
        validate_upload("avatar.png", b"", image_only=True)
    suffix, mime = validate_upload("avatar.png", PNG, image_only=True)
    assert (suffix, mime) == (".png", "image/png")


def test_allowlist_and_mime_tables_stay_consistent() -> None:
    """白名单、MIME 映射、纯文本集合三者必须对齐，否则会出现无法通过校验的扩展名。"""
    assert TEXT_EXTENSIONS <= UPLOAD_ALLOWED_EXTENSIONS
    assert IMAGE_EXTENSIONS <= UPLOAD_ALLOWED_EXTENSIONS
    assert set(EXTENSION_MIMES) <= UPLOAD_ALLOWED_EXTENSIONS
    # 一个扩展名不能同时是「无签名文本」和「有签名的二进制」
    assert not (TEXT_EXTENSIONS & set(EXTENSION_MIMES))
    # 图片扩展名必须在 MIME 映射里，否则校验时无从比对
    assert IMAGE_EXTENSIONS <= set(EXTENSION_MIMES)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.PNG", ".png"),
        ("dir/sub/a.tar.gz", ".gz"),
        ("C:\\x\\a.pdf", ".pdf"),
        ("noext", ""),
        ("trailing.", ""),
        ("a.toolongextension", ""),
        ("a.p p", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_extension(name: str | None, expected: str) -> None:
    assert normalize_extension(name) == expected


def test_resolve_mime_prefers_sniffed_then_extension_map() -> None:
    assert resolve_mime(".txt", "image/png") == "image/png"
    assert resolve_mime(".csv", None) == "text/csv"
    assert resolve_mime(".json", None) == "application/json"
    assert resolve_mime(".zip", None) == "application/octet-stream"
    assert resolve_mime(".pdf", "application/pdf") == "application/pdf"
