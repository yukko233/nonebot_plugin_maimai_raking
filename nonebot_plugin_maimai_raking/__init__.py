"""
nonebot-plugin-maimai-raking

一个基于 NoneBot2 的舞萌 DX 分群排行榜插件
"""
from nonebot import require, get_driver, on_command, get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.permission import SUPERUSER
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.message import event_preprocessor
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Message
from nonebot.typing import T_State

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .config import Config
from .database import Database
from .api import MaimaiAPI
from .render import render_ranking_image

__plugin_meta__ = PluginMetadata(
    name="舞萌排行榜",
    description="一个基于 NoneBot2 的舞萌 DX 分群排行榜插件",
    usage="""
    管理员命令：
    - 开启舞萌排行榜
    - 关闭舞萌排行榜
    - 刷新排行榜
    
    用户命令：
    - 加入排行榜 [QQ号]
    - 退出排行榜
    - wmrk <歌曲名/别名/ID>
    """,
    type="application",
    homepage="https://github.com/yourusername/nonebot-plugin-maimai-raking",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

# 加载配置
driver = get_driver()
config = get_plugin_config(Config)

# 初始化数据库和 API
db = Database(config.maimai_data_path)
api = MaimaiAPI(config.maimai_developer_token)

# 缓存群内昵称
group_nickname_cache: dict = {}

async def get_group_nickname(bot: Bot, qq: str, group_id: str) -> str:
    """获取群内昵称"""
    cache_key = f"{group_id}_{qq}"
    if cache_key in group_nickname_cache:
        return group_nickname_cache[cache_key]
    
    try:
        # 先尝试获取群成员信息（包含群名片）
        member_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(qq))
        # 群名片（card）优先，如果没有则使用QQ昵称（nickname）
        nickname = member_info.get("card") or member_info.get("nickname", qq)
        if not nickname.strip():  # 如果群名片为空字符串，使用QQ昵称
            nickname = member_info.get("nickname", qq)
        group_nickname_cache[cache_key] = nickname
        return nickname
    except Exception as e:
        logger.warning(f"获取群 {group_id} 中用户 {qq} 的群内昵称失败: {e}")
        # 如果获取群成员信息失败，尝试获取QQ昵称作为备用
        try:
            info = await bot.get_stranger_info(user_id=int(qq))
            nickname = info.get("nickname", qq)
            group_nickname_cache[cache_key] = nickname
            return nickname
        except Exception as e2:
            logger.warning(f"获取QQ {qq} 昵称也失败: {e2}")
            return qq

# ==================== 管理员命令 ====================

enable_ranking = on_command(
    "开启舞萌排行榜",
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    priority=5,
    block=True,
)

@enable_ranking.handle()
async def _(event: GroupMessageEvent):
    """开启舞萌排行榜功能"""
    group_id = str(event.group_id)
    db.enable_group(group_id)
    await enable_ranking.finish("✅ 已在本群开启舞萌排行榜功能！")


disable_ranking = on_command(
    "关闭舞萌排行榜",
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    priority=5,
    block=True,
)

@disable_ranking.handle()
async def _(event: GroupMessageEvent):
    """关闭舞萌排行榜功能"""
    group_id = str(event.group_id)
    db.disable_group(group_id)
    await disable_ranking.finish("❌ 已在本群关闭舞萌排行榜功能！")


refresh_ranking = on_command(
    "刷新排行榜",
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    priority=5,
    block=True,
)

@refresh_ranking.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """手动刷新排行榜"""
    group_id = str(event.group_id)
    
    if not db.is_group_enabled(group_id):
        await refresh_ranking.finish("本群未开启舞萌排行榜功能！")
        return
    
    await refresh_ranking.send("正在刷新排行榜数据，请稍候...")
    
    users = db.get_group_users(group_id)
    if not users:
        await refresh_ranking.finish("本群暂无用户加入排行榜！")
        return
    
    success_count = 0
    fail_count = 0
    
    for qq in users:
        try:
            records = await api.get_player_records(qq)
            if records:
                db.update_user_records(qq, records)
                success_count += 1
            else:
                fail_count += 1
                logger.warning(f"获取用户 {qq} 的成绩失败")
        except Exception as e:
            fail_count += 1
            logger.error(f"获取用户 {qq} 的成绩时出错: {e}")
    
    msg = f"刷新完成！\n成功: {success_count} 人\n失败: {fail_count} 人"
    await refresh_ranking.finish(msg)


# ==================== 用户命令 ====================

join_ranking = on_command("加入排行榜", priority=10, block=True)

@join_ranking.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """加入排行榜"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    
    if not db.is_group_enabled(group_id):
        await join_ranking.finish("本群未开启舞萌排行榜功能！")
        return
    
    # 解析 QQ 号参数
    arg_text = args.extract_plain_text().strip()
    qq = arg_text if arg_text else user_id
    
    # 如果指定了其他QQ号，需要管理员权限
    if arg_text and arg_text != user_id:
        # 检查权限：超管、群主、群管理员
        has_permission = False
        try:
            # 检查是否为超管
            if await SUPERUSER(bot, event):
                has_permission = True
            else:
                # 检查是否为群主或群管理员
                member_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(user_id))
                role = member_info.get("role", "member")
                if role in ["owner", "admin"]:
                    has_permission = True
        except Exception as e:
            logger.error(f"权限检查失败: {e}")
            await join_ranking.finish("❌ 权限检查失败，请稍后重试！")
            return
        
        if not has_permission:
            await join_ranking.finish("❌ 只有群主、管理员或超级管理员才能为他人加入排行榜！")
            return
    
    # 如果指定了其他QQ号，检查该用户是否在本群中
    if arg_text and arg_text != user_id:
        try:
            # 尝试获取群成员信息来验证用户是否在群中
            await bot.get_group_member_info(group_id=int(group_id), user_id=int(qq))
        except Exception as e:
            logger.warning(f"检查用户 {qq} 是否在群 {group_id} 中失败: {e}")
            await join_ranking.finish(f"❌ 用户 {qq} 不在本群中，无法加入排行榜！")
            return
    
    # 检查用户是否已经加入
    if db.is_user_in_group(qq, group_id):
        if qq == user_id:
            await join_ranking.finish("你已经在本群排行榜中了！")
        else:
            await join_ranking.finish(f"用户 {qq} 已经在本群排行榜中了！")
        return
    
    # 尝试获取用户数据验证
    try:
        records = await api.get_player_records(qq)
    except Exception as e:
        logger.error(f"获取用户 {qq} 的成绩时出错: {e}")
        await join_ranking.finish("❌ 加入排行榜失败，请稍后重试！")
        return
    
    if not records:
        await join_ranking.finish(
            "❌ 无法获取你的成绩数据！\n"
            "请确保：\n"
            "1. 已在水鱼查分器绑定此 QQ 号\n"
            "2. 已关闭隐私设置（允许第三方查询）\n"
            "3. QQ 号输入正确"
        )
        return
    
    # 添加用户到排行榜
    db.add_user_to_group(qq, group_id)
    db.update_user_records(qq, records)
    
    nickname = records.get("nickname", "未知")
    rating = records.get("rating", 0)
    
    if qq == user_id:
        await join_ranking.finish(
            f"✅ 已成功加入排行榜！\n"
            f"昵称: {nickname}\n"
            f"Rating: {rating}"
        )
    else:
        await join_ranking.finish(
            f"✅ 已成功为用户 {qq} 加入排行榜！\n"
            f"昵称: {nickname}\n"
            f"Rating: {rating}"
        )


leave_ranking = on_command("退出排行榜", priority=10, block=True)

@leave_ranking.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """退出排行榜"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    
    if not db.is_group_enabled(group_id):
        await leave_ranking.finish("本群未开启舞萌排行榜功能！")
        return
    
    # 解析 QQ 号参数
    arg_text = args.extract_plain_text().strip()
    qq = arg_text if arg_text else user_id
    
    # 如果指定了其他QQ号，需要管理员权限
    if arg_text and arg_text != user_id:
        # 检查权限：超管、群主、群管理员
        has_permission = False
        try:
            # 检查是否为超管
            if await SUPERUSER(bot, event):
                has_permission = True
            else:
                # 检查是否为群主或群管理员
                member_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(user_id))
                role = member_info.get("role", "member")
                if role in ["owner", "admin"]:
                    has_permission = True
        except Exception as e:
            logger.error(f"权限检查失败: {e}")
            await leave_ranking.finish("❌ 权限检查失败，请稍后重试！")
            return
        
        if not has_permission:
            await leave_ranking.finish("❌ 只有群主、管理员或超级管理员才能为他人退出排行榜！")
            return
    
    # 检查用户是否在排行榜中
    if not db.is_user_in_group(qq, group_id):
        if qq == user_id:
            await leave_ranking.finish("你还未加入本群排行榜！")
        else:
            await leave_ranking.finish(f"用户 {qq} 还未加入本群排行榜！")
        return
    
    # 从排行榜中移除用户
    db.remove_user_from_group(qq, group_id)
    
    if qq == user_id:
        await leave_ranking.finish("✅ 已退出本群排行榜！")
    else:
        await leave_ranking.finish(f"✅ 已成功为用户 {qq} 退出排行榜！")


query_ranking = on_command("wmrk", priority=10, block=True)

@query_ranking.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """查询歌曲排行榜"""
    group_id = str(event.group_id)
    
    if not db.is_group_enabled(group_id):
        await query_ranking.finish("本群未开启舞萌排行榜功能！")
        return
    
    query = args.extract_plain_text().strip()
    if not query:
        await query_ranking.finish("请输入歌曲名称、别名或 ID！\n例如: wmrk 群青\n可选难度: wmrk 群青 紫")
        return
    
    # 解析查询参数（歌曲名 + 可选难度）
    parts = query.split()
    
    # 难度映射
    difficulty_map = {
        "绿": 0,  # Basic
        "黄": 1,  # Advanced
        "红": 2,  # Expert
        "紫": 3,  # Master
        "白": 4,  # Re:Master
    }
    
    # 检查最后一个词是否为难度参数
    target_difficulty = None
    song_query = query
    
    if len(parts) > 1 and parts[-1] in difficulty_map:
        # 最后一个词是难度，将其分离
        target_difficulty = difficulty_map[parts[-1]]
        song_query = " ".join(parts[:-1])
    
    if not song_query:
        song_query = query
    
    # 获取歌曲信息
    try:
        song = await api.find_song(song_query)
    except Exception as e:
        logger.error(f"查找歌曲时出错: {e}")
        await query_ranking.finish("❌ 查询失败，请稍后重试！")
        return
    
    if not song:
        await query_ranking.finish(f"❌ 未找到歌曲: {song_query}")
        return
    
    song_id = int(song["id"])  # 确保转换为整数
    song_title = song["title"]
    
    # 获取群内用户的该歌曲成绩
    users = db.get_group_users(group_id)
    if not users:
        await query_ranking.finish("本群暂无用户加入排行榜！")
        return
    
    # 收集成绩数据
    ranking_data = []
    for qq in users:
        records = db.get_user_records(qq)
        if not records or "records" not in records:
            continue
        
        # 查找该歌曲的成绩
        for record in records["records"]:
            if record.get("song_id") == song_id:
                # 获取群内昵称
                group_nickname = await get_group_nickname(bot, qq, group_id)
                ranking_data.append({
                    "qq": qq,
                    "nickname": group_nickname,  # 使用群内昵称
                    "achievements": record.get("achievements", 0),
                    "fc": record.get("fc", ""),
                    "fs": record.get("fs", ""),
                    "level_label": record.get("level_label", ""),
                    "level_index": record.get("level_index", 0),
                    "ds": record.get("ds", 0),
                    "rate": record.get("rate", ""),
                })
    
    if not ranking_data:
        await query_ranking.finish(f"本群暂无人游玩过《{song_title}》！")
        return
    
    # 如果指定了难度，只显示该难度的成绩
    if target_difficulty is not None:
        ranking_data = [r for r in ranking_data if r["level_index"] == target_difficulty]
        if not ranking_data:
            difficulty_names = ["绿", "黄", "红", "紫", "白"]
            await query_ranking.finish(f"本群暂无人游玩过《{song_title}》的 {difficulty_names[target_difficulty]} 难度！")
            return
    else:
        # 默认：只显示最高难度的成绩
        max_difficulty = max(r["level_index"] for r in ranking_data)
        ranking_data = [r for r in ranking_data if r["level_index"] == max_difficulty]
    
    # 按成绩排序（降序）
    ranking_data.sort(key=lambda x: -x["achievements"])
    
    # 生成排行榜图片
    try:
        image_bytes = await render_ranking_image(song, ranking_data, api)
    except Exception as e:
        logger.error(f"生成排行榜图片时出错: {e}")
        await query_ranking.finish("❌ 生成图片失败，请稍后重试！")
        return
    
    msg = MessageSegment.image(image_bytes)
    await query_ranking.finish(msg)


# ==================== 定时任务 ====================

@scheduler.scheduled_job("cron", hour=0, minute=0, id="maimai_auto_update_records")
async def auto_update_records():
    """每天0点自动更新所有用户的成绩"""
    logger.info("开始自动更新舞萌排行榜数据...")
    
    all_users = db.get_all_users()
    success_count = 0
    fail_count = 0
    
    for qq in all_users:
        try:
            records = await api.get_player_records(qq)
            if records:
                db.update_user_records(qq, records)
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            logger.error(f"自动更新用户 {qq} 的成绩时出错: {e}")
    
    logger.info(f"自动更新成绩完成！成功: {success_count} 人，失败: {fail_count} 人")


@scheduler.scheduled_job("cron", hour=0, minute=5, id="maimai_auto_update_alias")
async def auto_update_alias():
    """每天0点05分自动更新别名数据"""
    logger.info("开始自动更新别名数据...")
    
    try:
        await api.load_alias_data_force()
        logger.info("别名数据自动更新完成！")
    except Exception as e:
        logger.error(f"自动更新别名数据时出错: {e}")


# ==================== 启动和关闭事件 ====================

@driver.on_startup
async def _():
    """插件启动时的初始化"""
    logger.info("舞萌排行榜插件已加载")
    # 预加载歌曲数据和别名数据
    await api.load_music_data()
    await api.load_alias_data()
    logger.info("歌曲数据和别名数据加载完成")


@driver.on_shutdown
async def _():
    """插件关闭时的清理"""
    logger.info("舞萌排行榜插件已卸载")