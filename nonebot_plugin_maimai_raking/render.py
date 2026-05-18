"""图片渲染模块 - 生成排行榜图片"""
from io import BytesIO
from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji
from pilmoji.source import GoogleEmojiSource
from pathlib import Path
import os
from nonebot.log import logger
from functools import lru_cache
import asyncio

# 模块级复用 GoogleEmojiSource 实例（避免每次渲染重新初始化 emoji 索引）
_emoji_source = GoogleEmojiSource()

# 图标文件夹路径
ICON_DIR = Path(__file__).parent / "icon"
# 自定义字体文件夹路径
FONT_DIR = Path(__file__).parent / "fonts"
# 确保字体文件夹存在
FONT_DIR.mkdir(exist_ok=True)

# 缓存配置
CACHE_SIZE = 100  # 缓存大小
COVER_CACHE_SIZE = 50  # 封面缓存大小

# 全局缓存字典
_icon_cache = {}
_font_cache = {}
_cover_cache = {}
_rounded_mask_cache = {}


@lru_cache(maxsize=CACHE_SIZE)
def _get_font_path() -> Optional[str]:
    """获取字体文件路径（带缓存）"""
    try:
        custom_fonts = list(FONT_DIR.glob("*.ttf")) + list(FONT_DIR.glob("*.ttc")) + list(FONT_DIR.glob("*.otf"))
        if custom_fonts:
            return str(custom_fonts[0])
        
        # 系统字体回退
        if os.name == 'nt':  # Windows
            return "msyh.ttc"
        else:  # Linux
            return "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
    except:
        return None


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """获取字体对象（带缓存）"""
    cache_key = f"{size}"
    if cache_key in _font_cache:
        return _font_cache[cache_key]
    
    font_path = _get_font_path()
    try:
        if font_path:
            font = ImageFont.truetype(font_path, size)
        else:
            font = ImageFont.load_default()
        
        _font_cache[cache_key] = font
        return font
    except:
        font = ImageFont.load_default()
        _font_cache[cache_key] = font
        return font


def _get_icon(icon_name: str, size: tuple) -> Optional[Image.Image]:
    """获取图标（带缓存）"""
    cache_key = f"{icon_name}_{size[0]}x{size[1]}"
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]
    
    icon_path = ICON_DIR / f"mmd_player_rtsong_{icon_name}.png"
    if not icon_path.exists():
        return None
    
    try:
        icon = Image.open(icon_path).convert("RGBA")
        icon = icon.resize(size, Image.Resampling.BILINEAR)
        _icon_cache[cache_key] = icon
        return icon
    except Exception:
        return None


def _get_rounded_mask(size: int) -> Image.Image:
    """获取圆角遮罩（带缓存）"""
    if size in _rounded_mask_cache:
        return _rounded_mask_cache[size]
    
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([(0, 0), (size, size)], radius=12, fill=255)
    
    _rounded_mask_cache[size] = mask
    return mask


async def _get_cached_cover(api, song_id: int, cover_size: int) -> Optional[Image.Image]:
    """获取缓存的封面 Image（已缩放 + 圆角遮罩，直接可 paste）"""
    if song_id in _cover_cache:
        return _cover_cache[song_id]
    
    if not api:
        return None
    
    try:
        cover_data = await api.get_song_cover(song_id)
        if cover_data:
            cover_img = Image.open(BytesIO(cover_data)).convert("RGBA")
            cover_img = cover_img.resize((cover_size, cover_size), Image.Resampling.BILINEAR)
            mask = _get_rounded_mask(cover_size)
            cover_img.putalpha(mask)
            
            # 限制缓存大小
            if len(_cover_cache) >= COVER_CACHE_SIZE:
                oldest_key = next(iter(_cover_cache))
                del _cover_cache[oldest_key]
            
            _cover_cache[song_id] = cover_img
            return cover_img
    except Exception as e:
        logger.warning(f"获取封面失败: {e}")
    
    return None


# 难度颜色
DIFF_COLORS = {
    0: (34, 139, 34),      # Basic - 绿色
    1: (255, 215, 0),      # Advanced - 黄色
    2: (255, 99, 71),      # Expert - 红色
    3: (153, 50, 204),     # Master - 紫色
    4: (238, 130, 238),    # Re:Master - 粉紫色
}

# 评级颜色
RATE_COLORS = {
    "d": (128, 128, 128),
    "c": (128, 128, 128),
    "b": (139, 69, 19),
    "bb": (139, 69, 19),
    "bbb": (139, 69, 19),
    "a": (34, 139, 34),
    "aa": (34, 139, 34),
    "aaa": (34, 139, 34),
    "s": (255, 215, 0),
    "sp": (255, 215, 0),
    "ss": (255, 165, 0),
    "ssp": (255, 165, 0),
    "sss": (255, 140, 0),
    "sssp": (218, 165, 32),
}


async def render_ranking_image(song: dict, ranking_data: List[Dict[str, Any]], api=None) -> bytes:
    """渲染排行榜图片
    
    Args:
        song: 歌曲信息
        ranking_data: 排行榜数据列表
        api: MaimaiAPI实例，用于获取封面
        
    Returns:
        图片字节数据
    """
    # 图片尺寸
    width = 850
    header_height = 240 # 增加高度以容纳所有难度定数显示
    row_height = 70
    footer_height = 70
    table_header_height = 50
    height = header_height + table_header_height + len(ranking_data) * row_height + footer_height
    
    # 创建图片
    img = Image.new("RGB", (width, height), color=(250, 250, 252))
    draw = ImageDraw.Draw(img)
    pilmoji = Pilmoji(img, source=_emoji_source)
    
    # 使用缓存的字体
    font_title = _get_font(32)
    font_normal = _get_font(24)
    font_small = _get_font(18)
    font_tiny = _get_font(17)
    
    # 简洁的背景设计
    song_type = song.get("type", "DX")
    
    # 使用浅色背景
    bg_color = (245, 245, 250)
    draw.rectangle([(0, 0), (width, header_height)], fill=bg_color)
    
    # 获取并绘制歌曲封面（简洁风格）
    cover_size = 197 # 封面大小（增大以与右边信息区域平齐）
    cover_x = 25      # 封面X位置
    cover_y = 25      # 封面Y位置
    
    if api:
        try:
            song_id = int(song.get("id", 0))
            cover_img = await _get_cached_cover(api, song_id, cover_size)
            if cover_img:
                img.paste(cover_img, (cover_x, cover_y), cover_img)
        except Exception as e:
            logger.warning(f"绘制封面失败: {e}")
    
    # 信息区域位置
    info_x = cover_x + cover_size + 20
    info_y = cover_y
    
    # 绘制歌曲标题（简洁风格）
    song_title = song.get("title", "未知歌曲")
    title_x = info_x
    title_y = info_y + 15
    
    # 深色文字，无阴影
    draw.text((title_x, title_y), song_title, font=font_title, fill=(40, 40, 40), anchor="lm")
    
    # 绘制歌曲ID（在标题下方）
    song_id = song.get("id", "未知")
    id_y = title_y + 40
    draw.text((title_x, id_y), f"ID: {song_id}", font=font_small, fill=(120, 120, 140), anchor="lm")
    
    # 绘制类型标签和版本标签（参考图片风格）
    tags_row1_y = title_y + 70  # 增加间距以容纳ID
    tag_x = info_x
    tag_height = 32
    gap = 10
    
    # 类型标签（如"DX谱面"）
    type_text = "DX谱面" if song_type == "DX" else "标准谱面"
    type_bg = (255, 228, 225) if song_type == "DX" else (230, 240, 255)
    type_text_color = (220, 100, 100) if song_type == "DX" else (100, 150, 220)
    
    type_bbox = draw.textbbox((0, 0), type_text, font=font_small)
    type_width = (type_bbox[2] - type_bbox[0]) + 20
    
    draw.rounded_rectangle(
        [(tag_x, tags_row1_y), (tag_x + type_width, tags_row1_y + tag_height)],
        radius=8, fill=type_bg
    )
    draw.text((tag_x + type_width // 2, tags_row1_y + tag_height // 2), type_text,
             font=font_small, fill=type_text_color, anchor="mm")
    
    # 版本标签（如"DX2025"）- 简化处理，暂时不显示具体版本
    
    # 绘制所有难度定数（参考图片：4个方块横向排列）
    tags_row2_y = tags_row1_y + tag_height + 15
    ds_box_size = 65  # 每个定数方块的尺寸
    ds_gap = 10
    
    # 获取歌曲的所有难度定数
    ds_values = song.get("ds", [])
    level_values = song.get("level", [])
    
    # 难度颜色（对应Basic/Advanced/Expert/Master/Re:Master）
    diff_colors_light = [
        (200, 255, 200),  # Basic - 浅绿
        (255, 245, 180),  # Advanced - 浅黄
        (255, 210, 210),  # Expert - 浅红
        (230, 210, 255),  # Master - 浅紫
        (255, 220, 255),  # Re:Master - 浅粉紫
    ]
    
    diff_text_colors = [
        (60, 150, 60),    # Basic
        (180, 150, 50),   # Advanced
        (200, 80, 80),    # Expert
        (130, 80, 180),   # Master
        (180, 100, 180),  # Re:Master
    ]
    
    # 深色边框颜色（每个难度对应的深色版本）
    diff_border_colors = [
        (40, 120, 40),    # Basic - 深绿
        (160, 120, 30),   # Advanced - 深橙黄
        (180, 50, 50),    # Expert - 深红
        (100, 50, 150),   # Master - 深紫
        (150, 70, 150),   # Re:Master - 深粉紫
    ]
    
    # 获取当前查询的难度索引（用于高亮显示）
    current_level_index = -1
    if ranking_data and len(ranking_data) > 0:
        current_level_index = ranking_data[0].get("level_index", -1)
    
    # 绘制所有难度（Basic, Advanced, Expert, Master, Re:Master）
    for i in range(min(5, len(ds_values))):
        if i < len(ds_values) and ds_values[i]:
            ds_val = ds_values[i]
            box_x = tag_x + i * (ds_box_size + ds_gap)
            
            # 绘制定数方块
            box_color = diff_colors_light[i] if i < len(diff_colors_light) else (220, 220, 220)
            text_color = diff_text_colors[i] if i < len(diff_text_colors) else (100, 100, 100)
            
            # 判断是否是当前查询的难度
            is_current = (i == current_level_index)
            
            if is_current:
                # 当前难度：添加对应颜色的深色边框
                border_color = diff_border_colors[i] if i < len(diff_border_colors) else (100, 100, 100)
                draw.rounded_rectangle(
                    [(box_x, tags_row2_y), (box_x + ds_box_size, tags_row2_y + ds_box_size)],
                    radius=10, fill=box_color, outline=border_color, width=3
                )
            else:
                # 非当前难度：无边框
                draw.rounded_rectangle(
                    [(box_x, tags_row2_y), (box_x + ds_box_size, tags_row2_y + ds_box_size)],
                    radius=10, fill=box_color
                )
            
            # 绘制定数数值（大字）
            ds_text = f"{ds_val:.1f}"
            draw.text((box_x + ds_box_size // 2, tags_row2_y + ds_box_size // 2),
                    ds_text, font=font_normal, fill=text_color, anchor="mm")
    
    # 绘制表头背景
    y_offset = header_height
    draw.rectangle([(0, y_offset), (width, y_offset + table_header_height)], fill=(240, 240, 245))
    
    # 绘制表头文字（移除了难度列）
    header_y = y_offset + table_header_height // 2
    draw.text((70, header_y), "排名", font=font_normal, fill=(80, 80, 100), anchor="mm")
    draw.text((200, header_y), "玩家", font=font_normal, fill=(80, 80, 100), anchor="mm")
    draw.text((450, header_y), "成绩", font=font_normal, fill=(80, 80, 100), anchor="mm")
    draw.text((620, header_y), "FC/FS", font=font_normal, fill=(80, 80, 100), anchor="mm")
    draw.text((750, header_y), "评级", font=font_normal, fill=(80, 80, 100), anchor="mm")
    
    y_offset += table_header_height
    
    # 直接按成绩排名（因为传入的数据已经是单一难度且已排序）
    for i, data in enumerate(ranking_data):
        rank = i + 1
        
        # 背景色（渐变交替 + 边框）
        if i % 2 == 0:
            bg_color = (255, 255, 255)
        else:
            bg_color = (248, 248, 252)
        
        # 绘制行背景（带圆角）
        margin = 15
        draw.rounded_rectangle(
            [(margin, y_offset + 5), (width - margin, y_offset + row_height - 5)],
            radius=8,
            fill=bg_color,
            outline=(220, 220, 230),
            width=1
        )
        
        # 排名（前三名特殊显示）
        rank_x = 70
        rank_y = y_offset + row_height // 2
        
        if rank == 1:
            # 金色第一名
            draw.text((rank_x, rank_y), "1st", font=font_normal, fill=(255, 215, 0), anchor="mm")
        elif rank == 2:
            # 银色第二名
            draw.text((rank_x, rank_y), "2nd", font=font_normal, fill=(192, 192, 192), anchor="mm")
        elif rank == 3:
            # 铜色第三名
            draw.text((rank_x, rank_y), "3rd", font=font_normal, fill=(205, 127, 50), anchor="mm")
        else:
            # 普通排名
            draw.text((rank_x, rank_y), str(rank), font=font_normal, fill=(100, 100, 120), anchor="mm")
        
        # 玩家昵称（根据长度调整字体和换行）
        nickname = data.get("nickname", "未知")
        nickname_x = 200
        nickname_y = y_offset + row_height // 2
        
        # 优化：预计算昵称长度
        nickname_len = len(nickname)
        
        if nickname_len > 8:
            # 长名字：使用小字体并换行
            if nickname_len <= 16:
                # 如果总长度不超过16，尝试平均分配
                mid_point = nickname_len // 2
                # 寻找最佳分割点（避免在字符中间分割）
                split_point = mid_point
                for i in range(max(1, mid_point - 2), min(nickname_len, mid_point + 3)):
                    if nickname[i] in ' -_':
                        split_point = i
                        break
                
                line1 = nickname[:split_point].strip()
                line2 = nickname[split_point:].strip()
                
                # 确保第一行不超过8个字符
                if len(line1) > 8:
                    line1 = nickname[:8]
                    line2 = nickname[8:]
            else:
                # 如果总长度超过16，第一行取8个字符
                line1 = nickname[:8]
                line2 = nickname[8:]
            
            # 如果第二行为空，则不分割
            if not line2.strip():
                line1 = nickname[:8] + "..." if nickname_len > 8 else nickname
                line2 = ""
            
            # 绘制第一行
            pilmoji.text((nickname_x, nickname_y - 8), line1, font=font_tiny, fill=(50, 50, 70), anchor="mm")
            # 绘制第二行（如果存在）
            if line2:
                # 如果第二行仍然超过8个字符，添加省略号
                if len(line2) > 8:
                    line2 = line2[:8] + "..."
                pilmoji.text((nickname_x, nickname_y + 8), line2, font=font_tiny, fill=(50, 50, 70), anchor="mm")
        else:
            # 短名字：使用正常字体
            pilmoji.text((nickname_x, nickname_y), nickname, font=font_normal, fill=(50, 50, 70), anchor="mm")
        
        # 成绩
        achievements = data.get("achievements", 0)
        fc = data.get("fc", "").lower()
        fs = data.get("fs", "").lower()
        
        # 成绩文本（加粗显示）
        score_text = f"{achievements:.4f}%"
        draw.text((450, y_offset + row_height // 2), score_text, font=font_normal, fill=(50, 50, 70), anchor="mm")
        
        # FC/FS 图标（始终两个位置，无图标时留白）
        icon_size = (35, 35)  # 正方形图标
        fc_fs_x = 620  # FC/FS 列的中心位置
        
        # 固定两个图标的总宽度
        total_width = 2 * icon_size[0] + 5  # 两个图标 + 一个间隙
        icon_x = fc_fs_x - total_width // 2
        
        # 绘制 FC 图标（有则绘制，无则占位）
        if fc:
            fc_icon = _get_icon(fc, icon_size)
            if fc_icon:
                img.paste(fc_icon, (icon_x, y_offset + row_height // 2 - icon_size[1] // 2), fc_icon)
        # 移动到下一个图标位置
        icon_x += icon_size[0] + 5
        
        # 绘制 FS 图标（有则绘制，无则占位）
        if fs:
            fs_icon = _get_icon(fs, icon_size)
            if fs_icon:
                img.paste(fs_icon, (icon_x, y_offset + row_height // 2 - icon_size[1] // 2), fs_icon)
        
        # 评级图标
        rate = data.get("rate", "").lower()
        if rate:
            rate_icon_size = (80, 36)  # 保持原始比例
            rate_icon = _get_icon(rate, rate_icon_size)
            if rate_icon:
                # 粘贴图标（居中）
                img.paste(rate_icon, (750 - rate_icon_size[0] // 2, y_offset + row_height // 2 - rate_icon_size[1] // 2), rate_icon)
        
        y_offset += row_height
    
    # 绘制页脚（带装饰线）
    footer_y = height - footer_height
    draw.line([(50, footer_y + 15), (width - 50, footer_y + 15)], fill=(200, 200, 220), width=1)
    
    draw.text(
        (width // 2, footer_y + 40),
        "舞萌排行榜 | Geneted by @MaiMaiRankingBot",
        font=font_small,
        fill=(150, 150, 170),
        anchor="mm"
    )
    
    # 转换为字节
    bio = BytesIO()
    # 快速压缩（compress_level=1 兼顾速度与体积）
    img.save(bio, format="PNG", optimize=True, compress_level=1)
    return bio.getvalue()


def clear_cache():
    """清理所有缓存"""
    global _icon_cache, _font_cache, _cover_cache, _rounded_mask_cache
    _icon_cache.clear()
    _font_cache.clear()
    _cover_cache.clear()
    _rounded_mask_cache.clear()
    _get_font_path.cache_clear()
    logger.info("已清理所有渲染缓存")


def clear_cover_memory_cache():
    """清理封面内存缓存（保留图标和字体缓存）"""
    global _cover_cache
    count = len(_cover_cache)
    _cover_cache.clear()
    logger.info(f"已清理 {count} 条封面内存缓存")
    return count


def get_cache_stats() -> dict:
    """获取缓存统计信息"""
    return {
        "icon_cache_size": len(_icon_cache),
        "font_cache_size": len(_font_cache),
        "cover_cache_size": len(_cover_cache),
        "mask_cache_size": len(_rounded_mask_cache),
        "font_path_cache_info": _get_font_path.cache_info()
    }


# ==================== 帮助图片渲染 ====================

HELP_CACHE_DIR = Path(__file__).parent / "help_cache"
HELP_CACHE_DIR.mkdir(exist_ok=True)

HELP_USER_CACHE = HELP_CACHE_DIR / "help_user.png"
HELP_ADMIN_CACHE = HELP_CACHE_DIR / "help_admin.png"

# 颜色方案
COLOR_PRIMARY = (108, 92, 231)      # 主色 - 紫色
COLOR_SECONDARY = (72, 126, 176)    # 辅助色 - 蓝色
COLOR_ACCENT = (255, 107, 107)      # 强调色 - 红色
COLOR_BG = (248, 249, 252)          # 背景色
COLOR_CARD_BG = (255, 255, 255)     # 卡片背景
COLOR_TEXT_PRIMARY = (30, 30, 50)   # 主文字色
COLOR_TEXT_SECONDARY = (100, 100, 130)  # 次要文字色
COLOR_TEXT_LIGHT = (150, 150, 180)  # 浅色文字
COLOR_BORDER = (230, 230, 240)      # 边框色
COLOR_SECTION_BG = (245, 245, 255)  # 分区背景色

# 帮助数据
HELP_USER_COMMANDS = [
    ("查询指令", [
        ("wmrk <歌曲名> [难度]", "查询歌曲排行榜，可选难度（绿/黄/红/紫/白）"),
        ("wmbm <歌曲名>", "查询歌曲详细信息（名称、ID、别名）"),
        ("wmrt [分段]", "查看本群 Rating 排行榜，如 wmrt5 查15000分段"),
    ]),
    ("个人管理", [
        ("加入排行榜 [QQ号/@用户]", "加入本群排行榜（管理员可代操作）"),
        ("退出排行榜 [QQ号/@用户]", "退出本群排行榜（管理员可代操作）"),
        ("刷新成绩", "刷新自己的成绩数据（每日限2次）"),
    ]),
]

HELP_ADMIN_COMMANDS = [
    ("群管理指令", [
        ("开启舞萌排行榜", "在本群开启排行榜功能"),
        ("关闭舞萌排行榜", "在本群关闭排行榜功能"),
        ("刷新群昵称 / 刷新昵称", "刷新本群所有用户的群名片昵称"),
        ("开启wmrt / 关闭wmrt", "开启或关闭本群的 Rating 排行榜功能"),
        ("wmbm+ <歌曲> <别名>", "为歌曲添加自定义别名"),
        ("wmbm- <歌曲> <别名>", "移除歌曲的自定义别名"),
    ]),
]


def _draw_rounded_rect(draw: ImageDraw, x: int, y: int, w: int, h: int, r: int, fill: tuple):
    """绘制圆角矩形"""
    draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=r, fill=fill)


def _draw_command_card(draw: ImageDraw, font_normal, font_small, x: int, y: int, cmd: str, desc: str, card_width: int):
    """绘制单个命令卡片"""
    card_height = 52
    card_x = x + 20
    card_y = y
    card_w = card_width - 40

    _draw_rounded_rect(draw, card_x, card_y, card_w, card_height, 8, COLOR_CARD_BG)

    draw.text((card_x + 16, card_y + 10), cmd, font=font_normal, fill=COLOR_PRIMARY)
    draw.text((card_x + 16, card_y + 30), desc, font=font_small, fill=COLOR_TEXT_SECONDARY)

    return card_height + 6


def _draw_section(draw: ImageDraw, font_title, font_normal, font_small, x: int, y: int, title: str, commands: list, card_width: int) -> int:
    """绘制一个分区"""
    section_padding = 16
    section_x = x + 20
    section_y = y
    section_w = card_width - 40
    section_h = 50 + len(commands) * 58

    _draw_rounded_rect(draw, section_x, section_y, section_w, section_h, 12, COLOR_SECTION_BG)

    draw.text((section_x + 20, section_y + 16), title, font=font_title, fill=COLOR_TEXT_PRIMARY)

    cmd_y = section_y + 50
    for cmd, desc in commands:
        cmd_y += _draw_command_card(draw, font_normal, font_small, section_x, cmd_y, cmd, desc, section_w)

    return section_h + 16


def render_help_image(is_admin: bool = False) -> bytes:
    """渲染帮助图片

    Args:
        is_admin: 是否为管理帮助

    Returns:
        图片字节数据
    """
    font_path = _get_font_path()
    if not font_path:
        logger.error("未找到可用字体，无法渲染帮助图片")
        return None

    font_title = _get_font(22)
    font_normal = _get_font(16)
    font_small = _get_font(13)
    font_header = _get_font(28)

    card_width = 520
    padding = 30

    commands = HELP_ADMIN_COMMANDS if is_admin else HELP_USER_COMMANDS

    content_height = sum(60 + len(section[1]) * 58 for section in commands)
    header_height = 80
    footer_height = 50
    total_height = header_height + content_height + footer_height + padding * 2

    img = Image.new("RGB", (card_width, total_height), COLOR_BG)
    draw = ImageDraw.Draw(img)
    pilmoji = Pilmoji(img, source=_emoji_source)

    # 绘制头部
    header_y = padding
    _draw_rounded_rect(draw, 20, header_y, card_width - 40, 60, 12, COLOR_PRIMARY)
    title_text = "舞萌排行榜 - 管理帮助" if is_admin else "舞萌排行榜 - 使用帮助"
    draw.text((card_width // 2, header_y + 30), title_text, font=font_header, fill=(255, 255, 255), anchor="mm")

    # 绘制各分区
    section_y = header_y + 80
    for title, cmds in commands:
        section_y += _draw_section(draw, font_title, font_normal, font_small, 0, section_y, title, cmds, card_width)

    # 绘制页脚
    footer_y = total_height - footer_height
    draw.line([(40, footer_y + 10), (card_width - 40, footer_y + 10)], fill=COLOR_BORDER, width=1)

    bio = BytesIO()
    img.save(bio, format="PNG", optimize=True, compress_level=1)
    return bio.getvalue()


def get_help_image(is_admin: bool = False) -> Optional[bytes]:
    """获取帮助图片（优先从缓存读取）

    Args:
        is_admin: 是否为管理帮助

    Returns:
        图片字节数据，失败返回 None
    """
    cache_path = HELP_ADMIN_CACHE if is_admin else HELP_USER_CACHE
    if cache_path.exists():
        try:
            with open(cache_path, "rb") as f:
                return f.read()
        except Exception as e:
            logger.warning(f"读取帮助图片缓存失败: {e}")
    return None


async def pre_render_help_images():
    """预渲染帮助图片并缓存到本地"""
    logger.info("开始预渲染帮助图片...")

    for is_admin in [False, True]:
        try:
            img_data = render_help_image(is_admin=is_admin)
            if img_data:
                cache_path = HELP_ADMIN_CACHE if is_admin else HELP_USER_CACHE
                with open(cache_path, "wb") as f:
                    f.write(img_data)
                logger.info(f"帮助图片已缓存: {cache_path}")
            else:
                logger.error(f"渲染帮助图片失败: {'管理' if is_admin else '用户'}")
        except Exception as e:
            logger.error(f"预渲染帮助图片时出错: {e}")

    logger.info("帮助图片预渲染完成")

