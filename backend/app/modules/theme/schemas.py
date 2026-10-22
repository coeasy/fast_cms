from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HEX_COLOR = r"^#[0-9a-fA-F]{6}$"
THEME_CODE = r"^[a-z][a-z0-9-]*$"


class ThemeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    color_primary: str = Field(default="#1677ff", alias="colorPrimary", pattern=HEX_COLOR)
    color_success: str = Field(default="#52c41a", alias="colorSuccess", pattern=HEX_COLOR)
    color_warning: str = Field(default="#faad14", alias="colorWarning", pattern=HEX_COLOR)
    color_error: str = Field(default="#ff4d4f", alias="colorError", pattern=HEX_COLOR)
    border_radius: int = Field(default=6, alias="borderRadius", ge=0, le=24)
    nav_theme: Literal["light", "realDark"] = Field(default="light", alias="navTheme")
    layout: Literal["side", "top", "mix"] = Field(default="side")
    dark_algorithm: bool = Field(default=False, alias="darkAlgorithm")
    compact_algorithm: bool = Field(default=False, alias="compactAlgorithm")
    fixed_header: bool = Field(default=True, alias="fixedHeader")
    split_menus: bool = Field(default=False, alias="splitMenus")
    color_weak: bool = Field(default=False, alias="colorWeak")
    color_bg_layout: str | None = Field(
        default=None, alias="colorBgLayout", pattern=HEX_COLOR
    )
    color_bg_container: str | None = Field(
        default=None, alias="colorBgContainer", pattern=HEX_COLOR
    )
    color_bg_sider: str | None = Field(
        default=None, alias="colorBgSider", pattern=HEX_COLOR
    )
    font_size: int = Field(default=14, alias="fontSize", ge=12, le=18)
    control_height: int = Field(default=32, alias="controlHeight", ge=24, le=48)
    component_size: Literal["small", "middle", "large"] = Field(
        default="middle", alias="componentSize"
    )
    color_link: str | None = Field(default=None, alias="colorLink", pattern=HEX_COLOR)
    color_bg_header: str | None = Field(
        default=None, alias="colorBgHeader", pattern=HEX_COLOR
    )
    content_padding: int = Field(default=24, alias="contentPadding", ge=0, le=64)
    sider_width: int = Field(default=208, alias="siderWidth", ge=120, le=360)
    show_breadcrumb: bool = Field(default=True, alias="showBreadcrumb")
    table_header_bg: str | None = Field(
        default=None, alias="tableHeaderBg", pattern=HEX_COLOR
    )
    # 紧凑算法会把字号与控件高度各降一档，表格密度改用显式开关保证所见即所得
    table_compact: bool = Field(default=False, alias="tableCompact")
    watermark: str = Field(default="", alias="watermark", max_length=32)


class ThemeCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=THEME_CODE)
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    config: ThemeConfig = Field(default_factory=ThemeConfig)
    sort: int = Field(default=0, ge=0, le=9999)


class ThemeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    config: ThemeConfig | None = None
    sort: int | None = Field(default=None, ge=0, le=9999)
