"""图片渲染模块 - 生成排行榜图片"""
from io import BytesIO
from typing import List, Dict, Any
from PIL import Image, ImageDraw, ImageFont
import os
from pathlib import Path
from nonebot.log import logger

# 图标文件夹路径
ICON_DIR = Path(__file__).parent / "icon"
# 自定义字体文件夹路径
FONT_DIR = Path(__file__).parent / "fonts"
# 确保字体文件夹存在
FONT_DIR.mkdir(exist_ok=True)


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
    
    # 尝试加载字体
    try:
        # 优先尝试加载自定义字体（支持表情符号和特殊符号的字体）
        # 推荐使用支持完整Unicode的字体，如 Noto Sans CJK SC、Microsoft YaHei UI、Segoe UI Emoji 等
        # 用户可以将字体文件放在 nonebot_plugin_maimai_raking/fonts 文件夹下
        custom_fonts = list(FONT_DIR.glob("*.ttf")) + list(FONT_DIR.glob("*.ttc"))
        if custom_fonts:
            # 使用找到的第一个自定义字体
            font_path = str(custom_fonts[0])
            font_title = ImageFont.truetype(font_path, 32)
            font_normal = ImageFont.truetype(font_path, 24)
            font_small = ImageFont.truetype(font_path, 18)
            font_tiny = ImageFont.truetype(font_path, 17)  # 用于长名字的小字体
        else:
            # Windows 字体
            font_title = ImageFont.truetype("msyh.ttc", 32)
            font_normal = ImageFont.truetype("msyh.ttc", 24)
            font_small = ImageFont.truetype("msyh.ttc", 18)
            font_tiny = ImageFont.truetype("msyh.ttc", 17)  # 用于长名字的小字体
    except:
        try:
            # Linux 字体
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 32)
            font_normal = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 24)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 18)
            font_tiny = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 17)  # 用于长名字的小字体
        except:
            # 使用默认字体
            font_title = ImageFont.load_default()
            font_normal = ImageFont.load_default()
            font_small = ImageFont.load_default()
            font_tiny = ImageFont.load_default()  # 用于长名字的小字体
    
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
            cover_data = await api.get_song_cover(song_id)
            if cover_data:
                from io import BytesIO
                cover_img = Image.open(BytesIO(cover_data)).convert("RGBA")
                # 调整封面大小
                cover_img = cover_img.resize((cover_size, cover_size), Image.Resampling.LANCZOS)
                
                # 创建圆角遮罩
                mask = Image.new("L", (cover_size, cover_size), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rounded_rectangle([(0, 0), (cover_size, cover_size)], radius=12, fill=255)
                
                # 应用圆角遮罩
                cover_img.putalpha(mask)
                
                # 粘贴封面（无阴影，简洁风格）
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
        
        if len(nickname) > 8:
            # 长名字：使用小字体并换行
            # 确保第一行不超过8个字符
            if len(nickname) <= 16:
                # 如果总长度不超过16，尝试平均分配
                mid_point = len(nickname) // 2
                # 寻找最佳分割点（避免在字符中间分割）
                split_point = mid_point
                for i in range(max(1, mid_point - 2), min(len(nickname), mid_point + 3)):
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
                line1 = nickname[:8] + "..." if len(nickname) > 8 else nickname
                line2 = ""
            
            # 绘制第一行
            draw.text((nickname_x, nickname_y - 8), line1, font=font_tiny, fill=(50, 50, 70), anchor="mm")
            # 绘制第二行（如果存在）
            if line2:
                # 如果第二行仍然超过8个字符，添加省略号
                if len(line2) > 8:
                    line2 = line2[:8] + "..."
                draw.text((nickname_x, nickname_y + 8), line2, font=font_tiny, fill=(50, 50, 70), anchor="mm")
        else:
            # 短名字：使用正常字体
            draw.text((nickname_x, nickname_y), nickname, font=font_normal, fill=(50, 50, 70), anchor="mm")
        
        # 成绩
        achievements = data.get("achievements", 0)
        fc = data.get("fc", "").lower()
        fs = data.get("fs", "").lower()
        
        # 成绩文本（加粗显示）
        score_text = f"{achievements:.4f}%"
        draw.text((450, y_offset + row_height // 2), score_text, font=font_normal, fill=(50, 50, 70), anchor="mm")
        
        # FC/FS 图标（新列）
        icon_size = (35, 35)  # 正方形图标
        fc_fs_x = 620  # FC/FS 列的中心位置
        
        # 计算需要显示的图标数量和起始位置
        icon_count = 0
        if fc:
            icon_count += 1
        if fs:
            icon_count += 1
        
        if icon_count > 0:
            # 居中显示图标
            total_width = icon_count * icon_size[0] + (icon_count - 1) * 5
            icon_x = fc_fs_x - total_width // 2
            
            if fc:
                fc_icon_path = ICON_DIR / f"mmd_player_rtsong_{fc}.png"
                if fc_icon_path.exists():
                    try:
                        fc_icon = Image.open(fc_icon_path).convert("RGBA")
                        fc_icon = fc_icon.resize(icon_size, Image.Resampling.LANCZOS)
                        img.paste(fc_icon, (icon_x, y_offset + row_height // 2 - icon_size[1] // 2), fc_icon)
                        icon_x += icon_size[0] + 5
                    except Exception:
                        pass
            
            if fs:
                fs_icon_path = ICON_DIR / f"mmd_player_rtsong_{fs}.png"
                if fs_icon_path.exists():
                    try:
                        fs_icon = Image.open(fs_icon_path).convert("RGBA")
                        fs_icon = fs_icon.resize(icon_size, Image.Resampling.LANCZOS)
                        img.paste(fs_icon, (icon_x, y_offset + row_height // 2 - icon_size[1] // 2), fs_icon)
                    except Exception:
                        pass
        
        # 评级图标
        rate = data.get("rate", "").lower()
        if rate:
            rate_icon_path = ICON_DIR / f"mmd_player_rtsong_{rate}.png"
            if rate_icon_path.exists():
                try:
                    rate_icon = Image.open(rate_icon_path).convert("RGBA")
                    # 调整图标大小（保持 200×89 的比例，稍微放大）
                    rate_icon_size = (80, 36)  # 保持原始比例
                    rate_icon = rate_icon.resize(rate_icon_size, Image.Resampling.LANCZOS)
                    # 粘贴图标（居中）
                    img.paste(rate_icon, (750 - rate_icon_size[0] // 2, y_offset + row_height // 2 - rate_icon_size[1] // 2), rate_icon)
                except Exception:
                    pass
        
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
    img.save(bio, format="PNG")
    return bio.getvalue()

