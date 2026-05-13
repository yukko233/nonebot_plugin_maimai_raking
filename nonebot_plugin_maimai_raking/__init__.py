"""
nonebot-plugin-maimai-raking

一个基于 NoneBot2 的舞萌 DX 分群排行榜插件
"""
from nonebot import require, get_driver, on_command, get_plugin_config, get_bots
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.permission import SUPERUSER
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.message import event_preprocessor
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Message
from nonebot.typing import T_State
from datetime import datetime

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .config import Config
from .database import Database
from .api import MaimaiAPI
from .render import render_ranking_image, clear_cover_memory_cache, get_help_image, pre_render_help_images

__plugin_meta__ = PluginMetadata(
    name="舞萌排行榜",
    description="一个基于 NoneBot2 的舞萌 DX 分群排行榜插件",
    usage=""" 
    超管命令：
    - 刷新排行榜
    - 重置刷新次数 <QQ号/@用户>
    - 更新歌曲数据
    - 清理缓存
    - 清理数据库
    
    管理员命令：
    - 开启舞萌排行榜
    - 关闭舞萌排行榜
    - 刷新群昵称
    - 刷新昵称
    - 加入排行榜 <QQ号/@用户>
    - 退出排行榜 <QQ号/@用户>
    
    用户命令：
    - 加入排行榜 [QQ号/@用户]
    - 退出排行榜 [QQ号/@用户]
    - 刷新成绩
    - wmrk <歌曲名/别名/ID> [难度]
    - wmbm <歌曲名/别名/ID>
    - wmrt [分段] - 查看本群 Rating 排行榜
      例如：wmrt 查询全部，wmrt5 查询15000分段
    - wmrk帮助 - 查看使用帮助
    - wmrk管理帮助 - 查看管理帮助
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

# 群昵称缓存
group_nickname_cache: dict = {}


def refresh_custom_alias_cache():
    """同步数据库中的自定义别名至 API 缓存"""
    custom_aliases = db.get_all_custom_aliases()
    api.set_custom_aliases(custom_aliases)


def _equals_ignore_case(a: str, b: str) -> bool:
    return a.lower() == b.lower()

async def get_group_nickname(bot: Bot, qq: str, group_id: str) -> str:
    """获取群内昵称（从缓存中获取）"""
    cache_key = f"{group_id}_{qq}"
    return group_nickname_cache.get(cache_key, qq)

async def update_group_nicknames(bot: Bot, group_id: str):
    """更新指定群的所有排行榜用户昵称，自动清理不存在的用户"""
    try:
        users = db.get_group_users(group_id)
        if not users:
            return
        
        logger.info(f"开始更新群 {group_id} 的 {len(users)} 个用户昵称")
        success_count = 0
        removed_count = 0
        
        for qq in users:
            try:
                # 获取群成员信息
                member_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(qq))
                # 群名片（card）优先，如果没有则使用QQ昵称（nickname）
                nickname = member_info.get("card") or member_info.get("nickname", qq)
                if not nickname.strip():  # 如果群名片为空字符串，使用QQ昵称
                    nickname = member_info.get("nickname", qq)
                
                # 更新缓存
                cache_key = f"{group_id}_{qq}"
                group_nickname_cache[cache_key] = nickname
                success_count += 1
            except Exception as e:
                # 检查错误是否是成员不存在
                error_str = str(e)
                if "成员不存在" in error_str or "retcode=1200" in error_str:
                    logger.warning(f"用户 {qq} 在群 {group_id} 不存在，自动清理")
                    db.remove_invalid_user_from_group(qq, group_id)
                    # 同时清理缓存
                    cache_key = f"{group_id}_{qq}"
                    if cache_key in group_nickname_cache:
                        del group_nickname_cache[cache_key]
                    removed_count += 1
                else:
                    logger.warning(f"更新群 {group_id} 中用户 {qq} 的昵称失败: {e}")
                    # 如果获取群成员信息失败，尝试获取QQ昵称作为备用
                    try:
                        info = await bot.get_stranger_info(user_id=int(qq))
                        nickname = info.get("nickname", qq)
                        cache_key = f"{group_id}_{qq}"
                        group_nickname_cache[cache_key] = nickname
                        success_count += 1
                    except Exception as e2:
                        logger.warning(f"获取QQ {qq} 昵称也失败: {e2}")
        
        logger.info(f"群 {group_id} 昵称更新完成，成功: {success_count}/{len(users)}，清理: {removed_count}")
    except Exception as e:
        logger.error(f"更新群 {group_id} 昵称时发生未预期的错误: {e}")
        raise

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
    permission=SUPERUSER,
    priority=5,
    block=True,
)

@refresh_ranking.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """手动刷新排行榜"""
    group_id = str(event.group_id)
    
    if not db.is_group_enabled(group_id):
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


refresh_nicknames = on_command(
    "刷新群昵称",
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    priority=5,
    block=True,
)

refresh_nickname = on_command(
    "刷新昵称",
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    priority=5,
    block=True,
)

reset_refresh_count = on_command(
    "重置刷新次数",
    permission=SUPERUSER,
    priority=5,
    block=True,
)

update_music_data = on_command(
    "更新歌曲数据",
    permission=SUPERUSER,
    priority=5,
    block=True,
)

@refresh_nicknames.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """手动刷新群昵称"""
    group_id = str(event.group_id)
    
    if not db.is_group_enabled(group_id):
        return
    
    users = db.get_group_users(group_id)
    if not users:
        await refresh_nicknames.finish("本群暂无用户加入排行榜！")
        return
    
    await refresh_nicknames.send(f"正在刷新群昵称，共 {len(users)} 位用户...")
    
    try:
        await update_group_nicknames(bot, group_id)
        # 使用 send 而不是 finish，避免 FinishedException
        await refresh_nicknames.send("✅ 群昵称刷新完成！")
    except Exception as e:
        logger.error(f"刷新群昵称失败: {e}")
        # 使用 send 而不是 finish，避免 FinishedException
        await refresh_nicknames.send("❌ 刷新群昵称失败，请稍后重试！")


@refresh_nickname.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """手动刷新群昵称"""
    group_id = str(event.group_id)
    
    if not db.is_group_enabled(group_id):
        return
    
    users = db.get_group_users(group_id)
    if not users:
        await refresh_nickname.finish("本群暂无用户加入排行榜！")
        return
    
    await refresh_nickname.send(f"正在刷新群昵称，共 {len(users)} 位用户...")
    
    try:
        await update_group_nicknames(bot, group_id)
        # 使用 send 而不是 finish，避免 FinishedException
        await refresh_nickname.send("✅ 群昵称刷新完成！")
    except Exception as e:
        logger.error(f"刷新群昵称失败: {e}")
        # 使用 send 而不是 finish，避免 FinishedException
        await refresh_nickname.send("❌ 刷新群昵称失败，请稍后重试！")


@reset_refresh_count.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """重置用户刷新次数"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    
    if not db.is_group_enabled(group_id):
        return
    
    # 解析参数：支持QQ号或@用户
    arg_text = args.extract_plain_text().strip()
    target_qq = None
    
    # 检查是否有@用户
    if event.message:
        for segment in event.message:
            if segment.type == "at":
                target_qq = str(segment.data.get("qq", ""))
                break
    
    # 确定目标QQ号
    if target_qq:
        # 有@用户，使用@的用户
        qq = target_qq
    elif arg_text:
        # 有文本参数，使用文本参数
        qq = arg_text
    else:
        await reset_refresh_count.finish("请指定要重置的用户！\n使用方法：\n• 重置刷新次数 <QQ号>\n• 重置刷新次数 @用户")
        return
    
    # 检查用户是否在排行榜中
    if not db.is_user_in_group(qq, group_id):
        await reset_refresh_count.finish(f"用户 {qq} 未加入本群排行榜！")
        return
    
    try:
        # 重置今日刷新次数
        today = datetime.now().strftime("%Y-%m-%d")
        db.reset_daily_refresh_count(qq, today)
        
        await reset_refresh_count.finish(f"✅ 已重置用户 {qq} 的今日刷新次数！")
        
    except Exception as e:
        logger.error(f"重置用户 {qq} 的刷新次数失败: {e}")
        await reset_refresh_count.finish("❌ 重置失败，请稍后重试！")


@update_music_data.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """更新水鱼歌曲数据"""
    await update_music_data.send("正在更新歌曲数据，请稍候...")
    
    try:
        await api.load_music_data()
        
        # 检查是否成功加载
        if api.music_data:
            song_count = len(api.music_data)
            await update_music_data.send(
                f"✅ 歌曲数据更新完成！\n"
                f"共加载 {song_count} 首歌曲"
            )
        else:
            await update_music_data.send("❌ 歌曲数据更新失败，未加载到任何歌曲数据！")
            
    except Exception as e:
        logger.error(f"更新歌曲数据时出错: {e}")
        await update_music_data.send("❌ 更新歌曲数据失败，请稍后重试！")


clear_cache_cmd = on_command(
    "清理缓存",
    permission=SUPERUSER,
    priority=5,
    block=True,
)

@clear_cache_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """清理所有歌曲封面缓存"""
    await clear_cache_cmd.send("正在清理缓存，请稍候...")
    
    try:
        # 清理数据库缓存
        db_count = api.clear_cover_cache()
        # 清理内存缓存
        memory_count = clear_cover_memory_cache()
        
        total_count = db_count + memory_count
        
        await clear_cache_cmd.send(
            f"✅ 缓存清理完成！\n"
            f"数据库缓存：{db_count} 条\n"
            f"内存缓存：{memory_count} 条\n"
            f"总计：{total_count} 条"
        )
        
    except Exception as e:
        logger.error(f"清理缓存时出错: {e}")
        await clear_cache_cmd.send("❌ 清理缓存失败，请稍后重试！")


refresh_records = on_command("刷新成绩", priority=10, block=True)

@refresh_records.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """刷新自己的成绩"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    
    if not db.is_group_enabled(group_id):
        return
    
    # 检查用户是否在排行榜中
    if not db.is_user_in_group(user_id, group_id):
        await refresh_records.finish("你还未加入本群排行榜！")
        return
    
    # 检查刷新频率限制（一个自然日内最多2次）
    today = datetime.now().strftime("%Y-%m-%d")
    last_update_time = db.get_last_update_time(user_id)
    
    if last_update_time:
        last_update_date = last_update_time.split("T")[0]  # 提取日期部分
        if last_update_date == today:
            # 检查今日刷新次数
            refresh_count = db.get_daily_refresh_count(user_id, today)
            if refresh_count >= 2:
                await refresh_records.finish(
                    "❌ 今日刷新次数已达上限！\n"
                    "每个自然日最多可刷新2次成绩\n"
                    "请明天再试，或联系管理员"
                )
                return
    
    await refresh_records.send("正在刷新你的成绩数据，请稍候...")
    
    try:
        # 获取最新成绩
        records = await api.get_player_records(user_id)
        if not records:
            await refresh_records.finish(
                "❌ 无法获取你的成绩数据！\n"
                "请确保：\n"
                "1. 已在水鱼查分器绑定此 QQ 号\n"
                "2. 已关闭隐私设置（允许第三方查询）\n"
                "3. 网络连接正常"
            )
            return
        
        # 更新成绩
        db.update_user_records(user_id, records)
        
        # 记录刷新操作
        db.log_refresh(user_id, today)
        
        # 获取更新后的信息
        nickname = records.get("nickname", "未知")
        rating = records.get("rating", 0)
        
        # 计算剩余刷新次数
        remaining_count = 2 - db.get_daily_refresh_count(user_id, today)
        
        # 发送成功消息
        await refresh_records.send(
            f"✅ 成绩刷新完成！\n"
            f"昵称: {nickname}\n"
            f"Rating: {rating}\n"
            f"今日剩余刷新次数: {remaining_count}/2"
        )
        
    except Exception as e:
        logger.error(f"刷新用户 {user_id} 的成绩时出错: {e}")
        # 使用 send 而不是 finish，避免 FinishedException
        await refresh_records.send("❌ 刷新成绩失败，请稍后重试！")


# ==================== 用户命令 ====================

join_ranking = on_command("加入排行榜", priority=10, block=True)

@join_ranking.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """加入排行榜"""
    current_group_id = str(event.group_id)
    user_id = str(event.user_id)
    
    # 解析参数：支持QQ号或@用户 + 可选的群号
    arg_text = args.extract_plain_text().strip()
    target_qq = None
    target_group_id = None
    
    # 检查是否有@用户
    if event.message:
        for segment in event.message:
            if segment.type == "at":
                target_qq = str(segment.data.get("qq", ""))
                break
    
    # 解析参数
    if arg_text:
        parts = arg_text.split()
        if target_qq:
            # 有@用户，第一个参数是@用户，后面的是群号
            if len(parts) >= 1:
                target_group_id = parts[0] if parts[0].isdigit() else None
        else:
            # 没有@用户，第一个参数是QQ号，第二个参数是群号（如果有）
            if len(parts) >= 1:
                if parts[0].isdigit():
                    target_qq = parts[0]
                    if len(parts) >= 2 and parts[1].isdigit():
                        target_group_id = parts[1]
    
    # 确定目标QQ号
    if target_qq:
        # 有@用户，使用@的用户
        qq = target_qq
    elif target_qq is None and arg_text:
        # 有文本参数，使用文本参数作为QQ号
        parts = arg_text.split()
        qq = parts[0] if parts and parts[0].isdigit() else user_id
    else:
        # 无参数，使用自己
        qq = user_id
    
    # 确定目标群号
    if target_group_id:
        # 指定了群号
        group_id = target_group_id
        # 检查是否为有效的群号格式
        if not group_id.isdigit():
            await join_ranking.finish("❌ 群号格式错误，请输入正确的群号！")
            return
    else:
        # 未指定群号，默认为当前群
        group_id = current_group_id
    
    # 如果指定了其他QQ号或群号，需要管理员权限
    # 特殊处理：如果指定了群号，则只有超管可以操作
    is_specified_group = group_id != current_group_id
    is_other_user = qq != user_id
    
    if is_other_user or is_specified_group:
        # 检查权限
        has_permission = False
        try:
            # 检查是否为超管
            if await SUPERUSER(bot, event):
                has_permission = True
            else:
                # 对于指定群的操作，仅允许超管
                if is_specified_group:
                    has_permission = False
                else:
                    # 检查是否为群主或群管理员（仅为他人加入当前群的情况）
                    member_info = await bot.get_group_member_info(group_id=int(current_group_id), user_id=int(user_id))
                    role = member_info.get("role", "member")
                    if role in ["owner", "admin"]:
                        has_permission = True
        except Exception as e:
            logger.error(f"权限检查失败: {e}")
            await join_ranking.finish("❌ 权限检查失败，请稍后重试！")
            return
        
        if not has_permission:
            if is_other_user and is_specified_group:
                await join_ranking.finish("❌ 只有超级管理员才能为他人加入指定群的排行榜！")
            elif is_other_user:
                await join_ranking.finish("❌ 只有群主、管理员或超级管理员才能为他人加入排行榜！")
            else:
                await join_ranking.finish("❌ 只有超级管理员才能加入指定群的排行榜！")
            return
    
    # 检查目标群是否启用了排行榜功能
    if not db.is_group_enabled(group_id):
        if group_id == current_group_id:
            await join_ranking.finish("❌ 当前群未启用排行榜功能！")
        else:
            await join_ranking.finish(f"❌ 群 {group_id} 未启用排行榜功能！")
        return
    
    # 如果指定了其他QQ号且是当前群，检查该用户是否在本群中
    if arg_text and arg_text != user_id and group_id == current_group_id:
        try:
            # 尝试获取群成员信息来验证用户是否在群中
            await bot.get_group_member_info(group_id=int(current_group_id), user_id=int(qq))
        except Exception as e:
            logger.warning(f"检查用户 {qq} 是否在群 {current_group_id} 中失败: {e}")
            await join_ranking.finish(f"❌ 用户 {qq} 不在本群中，无法加入排行榜！")
            return
    
    # 检查用户是否已经加入
    if db.is_user_in_group(qq, group_id):
        if qq == user_id:
            if group_id == current_group_id:
                await join_ranking.finish("你已经在本群排行榜中了！")
            else:
                await join_ranking.finish(f"你已经在群 {group_id} 的排行榜中了！")
        else:
            if group_id == current_group_id:
                await join_ranking.finish(f"用户 {qq} 已经在本群排行榜中了！")
            else:
                await join_ranking.finish(f"用户 {qq} 已经在群 {group_id} 的排行榜中了！")
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
    
    # 自动刷新该群所有成员的群昵称
    try:
        await update_group_nicknames(bot, group_id)
        logger.info(f"用户 {qq} 加入排行榜后，已自动刷新群 {group_id} 的所有成员昵称")
    except Exception as e:
        logger.warning(f"自动刷新群昵称失败: {e}")
    
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
    current_group_id = str(event.group_id)
    user_id = str(event.user_id)
    
    # 解析参数：支持QQ号或@用户 + 可选的群号
    arg_text = args.extract_plain_text().strip()
    target_qq = None
    target_group_id = None
    
    # 检查是否有@用户
    if event.message:
        for segment in event.message:
            if segment.type == "at":
                target_qq = str(segment.data.get("qq", ""))
                break
    
    # 解析参数
    if arg_text:
        parts = arg_text.split()
        if target_qq:
            # 有@用户，第一个参数是@用户，后面的是群号
            if len(parts) >= 1:
                target_group_id = parts[0] if parts[0].isdigit() else None
        else:
            # 没有@用户，第一个参数是QQ号，第二个参数是群号（如果有）
            if len(parts) >= 1:
                if parts[0].isdigit():
                    target_qq = parts[0]
                    if len(parts) >= 2 and parts[1].isdigit():
                        target_group_id = parts[1]
    
    # 确定目标QQ号
    if not target_qq:
        # 无指定QQ号，使用自己
        qq = user_id
    else:
        qq = target_qq
    
    # 确定目标群号
    if target_group_id:
        # 指定了群号
        group_id = target_group_id
        # 检查是否为有效的群号格式
        if not group_id.isdigit():
            await leave_ranking.finish("❌ 群号格式错误，请输入正确的群号！")
            return
    else:
        # 未指定群号，默认为当前群
        group_id = current_group_id
    
    # 如果指定了其他QQ号或群号，需要管理员权限
    # 特殊处理：如果指定了群号，则只有超管可以操作
    is_specified_group = group_id != current_group_id
    is_other_user = qq != user_id
    
    if is_other_user or is_specified_group:
        # 检查权限
        has_permission = False
        try:
            # 检查是否为超管
            if await SUPERUSER(bot, event):
                has_permission = True
            else:
                # 对于指定群的操作，仅允许超管
                if is_specified_group:
                    has_permission = False
                else:
                    # 检查是否为群主或群管理员（仅为他人退出当前群的情况）
                    member_info = await bot.get_group_member_info(group_id=int(current_group_id), user_id=int(user_id))
                    role = member_info.get("role", "member")
                    if role in ["owner", "admin"]:
                        has_permission = True
        except Exception as e:
            logger.error(f"权限检查失败: {e}")
            await leave_ranking.finish("❌ 权限检查失败，请稍后重试！")
            return
        
        if not has_permission:
            if is_other_user and is_specified_group:
                await leave_ranking.finish("❌ 只有超级管理员才能为他人退出指定群的排行榜！")
            elif is_other_user:
                await leave_ranking.finish("❌ 只有群主、管理员或超级管理员才能为他人退出排行榜！")
            else:
                await leave_ranking.finish("❌ 只有超级管理员才能退出指定群的排行榜！")
            return
    
    # 检查目标群是否启用了排行榜功能
    if not db.is_group_enabled(group_id):
        if group_id == current_group_id:
            await leave_ranking.finish("❌ 当前群未启用排行榜功能！")
        else:
            await leave_ranking.finish(f"❌ 群 {group_id} 未启用排行榜功能！")
        return
    
    # 检查用户是否在排行榜中
    if not db.is_user_in_group(qq, group_id):
        if qq == user_id:
            if group_id == current_group_id:
                await leave_ranking.finish("你还未加入本群排行榜！")
            else:
                await leave_ranking.finish(f"你还未加入群 {group_id} 的排行榜！")
        else:
            if group_id == current_group_id:
                await leave_ranking.finish(f"用户 {qq} 还未加入本群排行榜！")
            else:
                await leave_ranking.finish(f"用户 {qq} 还未加入群 {group_id} 的排行榜！")
        return
    
    # 从排行榜中移除用户
    db.remove_user_from_group(qq, group_id)
    
    if qq == user_id:
        if group_id == current_group_id:
            await leave_ranking.finish("✅ 已退出本群排行榜！")
        else:
            await leave_ranking.finish(f"✅ 已退出群 {group_id} 的排行榜！")
    else:
        if group_id == current_group_id:
            await leave_ranking.finish(f"✅ 已成功为用户 {qq} 退出本群排行榜！")
        else:
            await leave_ranking.finish(f"✅ 已成功为用户 {qq} 退出群 {group_id} 的排行榜！")


query_ranking = on_command("wmrk", priority=10, block=True)
query_song_info = on_command("wmbm", priority=10, block=True)
query_rating_ranking = on_command("wmrt", priority=10, block=True)

add_alias_command = on_command(
    "wmbm+",
    priority=10,
    block=True,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
)

remove_alias_command = on_command(
    "wmbm-",
    priority=10,
    block=True,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
)

# wmrt功能开关命令，仅允许群主、管理员和超管操作
toggle_wmrt = on_command("开启wmrt", aliases={"关闭wmrt"}, priority=10, block=True, 
                        permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER)

@query_ranking.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """查询歌曲排行榜"""
    group_id = str(event.group_id)
    
    if not db.is_group_enabled(group_id):
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
        await query_ranking.finish("❌ 未找到歌曲")
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
    
    # 限制显示前20名
    ranking_data = ranking_data[:20]
    
    # 生成排行榜图片
    try:
        image_bytes = await render_ranking_image(song, ranking_data, api)
    except Exception as e:
        logger.error(f"生成排行榜图片时出错: {e}")
        await query_ranking.finish("❌ 生成图片失败，请稍后重试！")
        return
    
    msg = MessageSegment.image(image_bytes)
    await query_ranking.finish(msg)


@query_song_info.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """查询歌曲信息（名称、ID、别名）"""
    query = args.extract_plain_text().strip()
    if not query:
        await query_song_info.finish("请输入歌曲名称、别名或 ID！\n例如: wmbm 群青")
        return
    
    # 获取歌曲信息
    try:
        song = await api.find_song(query)
    except Exception as e:
        logger.error(f"查找歌曲时出错: {e}")
        await query_song_info.finish("❌ 查询失败，请稍后重试！")
        return
    
    if not song:
        await query_song_info.finish("❌ 未找到歌曲，请检查歌曲名称或尝试其他关键词")
        return
    
    song_id = int(song["id"])
    song_title = song["title"]
    song_type = song.get("type", "DX")
    
    # 查找该歌曲的所有别名
    aliases = []
    for alias_item in api.alias_data:
        if "SongID" in alias_item and alias_item["SongID"] == song_id:
            if "Alias" in alias_item and isinstance(alias_item["Alias"], list):
                aliases.extend(alias_item["Alias"])
    
    # 去重并排序
    aliases = sorted(list(set(aliases)))
    
    # 构建返回消息
    result = f"🎵 歌曲信息\n"
    result += f"📝 名称: {song_title}\n"
    result += f"🆔 ID: {song_id}\n"
    # 显示谱面类型，将SD改为标准
    type_display = "DX谱面" if song_type == "DX" else "标准谱面"
    result += f"📊 类型: {type_display}\n"
    
    if aliases:
        result += f"🏷️ 别名 ({len(aliases)}个):\n"
        # 每个别名单独一行
        for alias in aliases:
            result += f"{alias}\n"
    else:
        result += "🏷️ 别名: 暂无别名\n"
    
    await query_song_info.finish(result)


@add_alias_command.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """为歌曲新增自定义别名"""
    group_id = str(event.group_id)

    if not db.is_group_enabled(group_id):
        return

    arg_text = args.extract_plain_text().strip()
    if not arg_text:
        await add_alias_command.finish("请提供歌曲关键词和要添加的别名。\n格式：wmbm+ 歌曲关键词 新别名")
        return

    if not api.alias_data:
        await api.load_alias_data()
        refresh_custom_alias_cache()

    parts = arg_text.rsplit(maxsplit=1)
    if len(parts) < 2:
        await add_alias_command.finish("格式错误！请使用：wmbm+ 歌曲关键词 新别名")
        return

    song_query = parts[0].strip()
    new_alias = parts[1].strip()

    if not song_query or not new_alias:
        await add_alias_command.finish("歌曲关键词和新别名均不能为空。")
        return

    if len(new_alias) > 40:
        await add_alias_command.finish("别名过长，请控制在40个字符以内。")
        return

    try:
        song = await api.find_song(song_query)
    except Exception as e:
        logger.error(f"查找歌曲时出错: {e}")
        await add_alias_command.finish("❌ 查询失败，请稍后重试！")
        return

    if not song:
        await add_alias_command.finish("❌ 未找到对应歌曲，请检查输入。")
        return

    song_id = int(song["id"])
    song_title = song.get("title", "未知")

    if _equals_ignore_case(song_title, new_alias):
        await add_alias_command.finish("别名不能与歌曲原名完全相同。")
        return

    existing_song_id = api.find_song_id_by_alias(new_alias)
    if existing_song_id:
        if existing_song_id == song_id:
            await add_alias_command.finish("该别名已存在于当前歌曲中，无需重复添加。")
        else:
            await add_alias_command.finish("该别名已被其他歌曲使用，无法重复添加。")
        return

    success = db.add_custom_alias(song_id, new_alias)
    if not success:
        await add_alias_command.finish("添加别名失败，可能已存在同名别名。")
        return

    api.add_custom_alias(song_id, new_alias)

    custom_aliases = db.get_custom_aliases(song_id)
    custom_display = "、".join(custom_aliases) if custom_aliases else "无"

    msg = (
        "✅ 别名添加成功！\n"
        f"歌曲: {song_title}\n"
        f"新增别名: {new_alias}\n"
        f"当前自定义别名: {custom_display}"
    )
    await add_alias_command.finish(msg)


@remove_alias_command.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """移除歌曲的自定义别名"""
    group_id = str(event.group_id)

    if not db.is_group_enabled(group_id):
        return

    arg_text = args.extract_plain_text().strip()
    if not arg_text:
        await remove_alias_command.finish("请提供歌曲关键词和要删除的别名。\n格式：wmbm- 歌曲关键词 目标别名")
        return

    if not api.alias_data:
        await api.load_alias_data()
        refresh_custom_alias_cache()

    parts = arg_text.rsplit(maxsplit=1)
    if len(parts) < 2:
        await remove_alias_command.finish("格式错误！请使用：wmbm- 歌曲关键词 目标别名")
        return

    song_query = parts[0].strip()
    target_alias = parts[1].strip()

    if not song_query or not target_alias:
        await remove_alias_command.finish("歌曲关键词和目标别名均不能为空。")
        return

    try:
        song = await api.find_song(song_query)
    except Exception as e:
        logger.error(f"查找歌曲时出错: {e}")
        await remove_alias_command.finish("❌ 查询失败，请稍后重试！")
        return

    if not song:
        await remove_alias_command.finish("❌ 未找到对应歌曲，请检查输入。")
        return

    song_id = int(song["id"])
    song_title = song.get("title", "未知")

    custom_aliases = db.get_custom_aliases(song_id)
    if not custom_aliases:
        await remove_alias_command.finish("该歌曲暂无自定义别名。")
        return

    if not any(_equals_ignore_case(alias, target_alias) for alias in custom_aliases):
        await remove_alias_command.finish("未找到要移除的自定义别名。")
        return

    success = db.remove_custom_alias(song_id, target_alias)
    if not success:
        await remove_alias_command.finish("移除别名失败，请稍后再试。")
        return

    api.remove_custom_alias(song_id, target_alias)

    remaining_aliases = db.get_custom_aliases(song_id)
    remaining_display = "、".join(remaining_aliases) if remaining_aliases else "无"

    msg = (
        "✅ 别名已移除！\n"
        f"歌曲: {song_title}\n"
        f"已移除别名: {target_alias}\n"
        f"当前自定义别名: {remaining_display}"
    )
    await remove_alias_command.finish(msg)


@toggle_wmrt.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """切换wmrt功能开关"""
    group_id = str(event.group_id)
    command = event.get_message().extract_plain_text().strip()
    
    
    # 根据命令切换wmrt功能开关
    if command == "开启wmrt":
        db.enable_wmrt(group_id)
        await toggle_wmrt.finish("✅ 已开启本群的Rating排行榜功能！")
    elif command == "关闭wmrt":
        db.disable_wmrt(group_id)
        await toggle_wmrt.finish("✅ 已关闭本群的Rating排行榜功能！")


@query_rating_ranking.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """查询群内 Rating 排行榜"""
    group_id = str(event.group_id)
    


    
    # 解析分段参数
    arg_text = args.extract_plain_text().strip()
    rating_segment = None
    min_rating = 0
    max_rating = 999999
    segment_display = "全部"
    
    if arg_text:
        # 尝试解析分段参数（例如：wmrt5 表示 15000+ 分段）
        try:
            segment = int(arg_text)
            if 0 <= segment <= 7:
                rating_segment = segment
                if segment == 0:
                    min_rating = 10000
                    max_rating = 10999
                    segment_display = "10000~10999"
                else:
                    min_rating = segment * 1000 + 10000  # 1->11000, 2->12000, ..., 7->17000
                    max_rating = min_rating + 999  # 例如 15000-15999
                    segment_display = f"{min_rating}~{max_rating}"
            else:
                await query_rating_ranking.finish(
                    "❌ 分段参数错误！\n"
                    "请使用 0-7 的数字，例如：\n"
                    "• wmrt0 - 查询 10000-10999 分段\n"
                    "• wmrt5 - 查询 15000-15999 分段\n"
                    "• wmrt7 - 查询 17000-17999 分段\n"
                    "• wmrt - 查询全部玩家"
                )
                return
        except ValueError:
            await query_rating_ranking.finish(
                "❌ 参数格式错误！\n"
                "请使用数字参数，例如：\n"
                "• wmrt0 - 查询 10000-10999 分段\n"
                "• wmrt5 - 查询 15000-15999 分段\n"
                "• wmrt - 查询全部玩家"
            )
            return
    
    # 获取群内用户
    users = db.get_group_users(group_id)
    if not users:
        await query_rating_ranking.finish("本群暂无用户加入排行榜！")
        return
    
    # 收集用户 Rating 数据
    rating_data = []
    for qq in users:
        records = db.get_user_records(qq)
        if not records:
            continue
        
        rating = records.get("rating", 0)
        nickname = records.get("nickname", "未知")
        
        # 如果指定了分段，只统计该分段的玩家
        if rating_segment is not None:
            if not (min_rating <= rating <= max_rating):
                continue
        
        # 获取群内昵称
        group_nickname = await get_group_nickname(bot, qq, group_id)
        
        rating_data.append({
            "qq": qq,
            "nickname": group_nickname,
            "maimai_nickname": nickname,
            "rating": rating
        })
    
    if not rating_data:
        if rating_segment is not None:
            await query_rating_ranking.finish(f"本群 {segment_display} 分段暂无玩家！")
        else:
            await query_rating_ranking.finish("本群暂无用户有成绩记录！")
        return
    
    # 按 rating 降序排序
    rating_data.sort(key=lambda x: x["rating"], reverse=True)
    
    # 取前十名
    top_10 = rating_data[:10]
    
    # 构建返回消息
    if rating_segment is not None:
        result = f"🏆 本群 Rating 排行榜 W{rating_segment} TOP {len(top_10)}\n"
    else:
        result = f"🏆 本群 Rating 排行榜 TOP {len(top_10)}\n"
    
    result += "=" * 30 + "\n"
    
    for i, data in enumerate(top_10, 1):
        # 排名图标
        if i == 1:
            rank_icon = "🥇"
        elif i == 2:
            rank_icon = "🥈"
        elif i == 3:
            rank_icon = "🥉"
        else:
            rank_icon = f"{i}."
        
        nickname = data["nickname"]
        rating = data["rating"]
        
        # 昵称长度限制：超过12字添加省略号
        if len(nickname) > 12:
            nickname = nickname[:12] + "..."
        
        result += f"{rank_icon} {nickname}\n"
        result += f"   Rating: {rating}\n"
    
    result += "=" * 30
    
    # 如果该分段有更多玩家，显示总人数
    if len(rating_data) > 10:
        result += f"\n该分段共 {len(rating_data)} 人"
    
    await query_rating_ranking.finish(result)


# ==================== 定时任务 ====================

# ==================== 超管命令 ====================

clean_database = on_command(
    "清理数据库",
    permission=SUPERUSER,
    priority=5,
    block=True,
)

@clean_database.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """清理已退出群组的数据（仅超管可用）"""
    # 发送清理消息
    try:
        await clean_database.send("正在清理已退出群组的数据，请稍候...")
    except Exception as e:
        logger.error(f"发送清理消息时出错: {e}")
        # 如果发送消息失败，直接返回而不继续执行
        return
    
    try:
        # 获取当前机器人加入的所有群组
        groups = await bot.get_group_list()
        current_group_ids = [str(group["group_id"]) for group in groups]
        
        # 清理数据库中已退出的群组数据
        cleaned_count = db.clean_left_groups(current_group_ids)
        
        if cleaned_count > 0:
            await clean_database.finish(f"✅ 清理完成！共清理了 {cleaned_count} 个已退出群组的数据。")
        else:
            await clean_database.finish("✅ 数据库清理完成！没有发现已退出群组。")
    except FinishedException:
        # 如果是FinishedException异常，说明已经调用了finish()，直接返回
        return
    except Exception as e:
        logger.error(f"清理数据库时出错: {e}", exc_info=True)
        try:
            await clean_database.finish("❌ 清理数据库时发生错误，请查看日志！")
        except FinishedException:
            # 如果是FinishedException异常，说明已经调用了finish()，直接返回
            return
        except Exception as send_error:
            # 如果发送错误消息也失败，至少记录日志
            logger.error(f"发送错误消息时也出错: {send_error}", exc_info=True)
        return


# ==================== 帮助命令 ====================

help_cmd = on_command("wmrk帮助", priority=8, block=True)
help_admin_cmd = on_command("wmrk管理帮助", priority=8, block=True)


@help_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """显示普通用户帮助"""
    img_data = get_help_image(is_admin=False)
    if img_data:
        await help_cmd.finish(MessageSegment.image(img_data))
    else:
        await help_cmd.finish("❌ 帮助图片未生成，请联系管理员")


@help_admin_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """显示管理帮助"""
    img_data = get_help_image(is_admin=True)
    if img_data:
        await help_admin_cmd.finish(MessageSegment.image(img_data))
    else:
        await help_admin_cmd.finish("❌ 帮助图片未生成，请联系管理员")


# ==================== 定时任务 ====================

@scheduler.scheduled_job("cron", hour=0, minute=15, id="maimai_auto_update_records")
async def auto_update_records():
    """每天0点15分自动更新所有用户的成绩"""
    logger.info("开始自动更新舞萌排行榜数据...")
    
    all_users = db.get_all_users()
    success_count = 0
    fail_count = 0
    today = datetime.now().strftime("%Y-%m-%d")
    
    for qq in all_users:
        # 如果当日已有手动刷新记录，则跳过自动更新
        if db.get_daily_refresh_count(qq, today) > 0:
            logger.info(f"用户 {qq} 当日已有手动刷新记录，跳过自动更新")
            continue
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
        refresh_custom_alias_cache()
        logger.info("别名数据自动更新完成！")
    except Exception as e:
        logger.error(f"自动更新别名数据时出错: {e}")


@scheduler.scheduled_job("cron", hour=0, minute=10, id="maimai_auto_update_nicknames")
async def auto_update_nicknames():
    """每天0点10分自动更新所有启用群的昵称"""
    logger.info("开始自动更新群昵称...")
    
    try:
        # 获取所有连接的 bot
        bots = get_bots()
        if not bots:
            logger.warning("没有连接的 bot，跳过昵称更新")
            return
        
        # 获取所有启用的群
        enabled_groups = db.get_all_enabled_groups()
        if not enabled_groups:
            logger.info("没有启用的群，跳过昵称更新")
            return
        
        logger.info(f"开始更新 {len(enabled_groups)} 个群的用户昵称")
        success_count = 0
        fail_count = 0
        
        # 遍历所有 bot（通常只有一个）
        for bot_id, bot in bots.items():
            for group_id in enabled_groups:
                try:
                    await update_group_nicknames(bot, group_id)
                    success_count += 1
                except Exception as e:
                    fail_count += 1
                    logger.warning(f"更新群 {group_id} 昵称失败: {e}")
        
        logger.info(f"自动更新群昵称完成！成功: {success_count} 个群，失败: {fail_count} 个群")
    except Exception as e:
        logger.error(f"自动更新群昵称时出错: {e}")


@scheduler.scheduled_job("cron", hour=0, minute=0, id="maimai_auto_update_music_data")
async def auto_update_music_data():
    """每天0点自动更新歌曲数据"""
    logger.info("开始自动更新歌曲数据...")
    
    try:
        await api.load_music_data()
        if api.music_data:
            song_count = len(api.music_data)
            logger.info(f"歌曲数据自动更新完成！共加载 {song_count} 首歌曲")
        else:
            logger.warning("歌曲数据自动更新失败，未加载到任何歌曲数据！")
    except Exception as e:
        logger.error(f"自动更新歌曲数据时出错: {e}")


# ==================== 启动和关闭事件 ====================

@driver.on_startup
async def _():
    """插件启动时的初始化"""
    logger.info("舞萌排行榜插件已加载")
    # 预加载歌曲数据和别名数据
    await api.load_music_data()
    await api.load_alias_data()
    refresh_custom_alias_cache()
    logger.info("歌曲数据和别名数据加载完成")
    
    # 预渲染帮助图片
    await pre_render_help_images()


@driver.on_bot_connect
async def _(bot: Bot):
    """Bot连接成功后的初始化"""
    logger.info("Bot已连接，开始初始化用户昵称缓存")
    
    try:
        enabled_groups = db.get_all_enabled_groups()
        if not enabled_groups:
            logger.info("没有启用的群，跳过昵称缓存初始化")
            return
        
        logger.info(f"开始初始化 {len(enabled_groups)} 个群的用户昵称缓存")
        
        for group_id in enabled_groups:
            try:
                await update_group_nicknames(bot, group_id)
            except Exception as e:
                logger.warning(f"更新群 {group_id} 昵称失败: {e}")
                continue  # 继续处理其他群
        
        logger.info("用户昵称缓存初始化完成")
    except Exception as e:
        logger.warning(f"初始化用户昵称缓存失败: {e}")


@driver.on_shutdown
async def _():
    """插件关闭时的清理"""
    logger.info("舞萌排行榜插件已卸载")