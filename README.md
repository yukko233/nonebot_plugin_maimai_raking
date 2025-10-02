# nonebot-plugin-maimai-raking

一个基于 NoneBot2 的舞萌 DX 分群排行榜插件

## 功能特性

- 🎮 **分群管理**：每个群组独立管理排行榜数据，互不干扰
- 🔐 **权限控制**：超级管理员、群管理员、群主可以开关功能和管理成员
- 📊 **排行榜系统**：用户可自愿加入/退出群内排行榜，支持管理员为他人操作
- 🔄 **自动更新**：
  - 每天凌晨 0 点自动更新所有用户的成绩数据
  - 每天凌晨 0 点 05 分自动更新歌曲别名库
- 🎵 **歌曲查询**：支持歌曲名、别名、ID 查询群内排行，支持指定难度查询
- 🖼️ **图片输出**：美观的排行榜图片展示，包含封面、难度、成绩、评级等信息
- 💾 **智能缓存**：本地缓存歌曲封面和别名数据，减少网络请求
- 👥 **昵称显示**：优先显示群名片，提升用户识别度

## 安装

```bash
pip install nonebot-plugin-maimai-raking
```

或使用 nb-cli：

```bash
nb plugin install nonebot-plugin-maimai-raking
```

## 配置

在 `.env` 文件中添加以下配置：

```env
# 水鱼查分器 Developer Token（必填）
MAIMAI_DEVELOPER_TOKEN=your_developer_token_here

# 数据存储路径（可选，默认为 data/maimai_raking）
MAIMAI_DATA_PATH=data/maimai_raking
```

### 获取 Developer Token

1. 访问 [水鱼查分器](https://www.diving-fish.com/maimaidx/prober/)
2. 登录你的账号
3. 进入"编辑个人资料"页面
4. 在"需要查分器中的玩家数据用于其他应用程序开发？请点击这里~"中申请 Developer Token

## 使用方法

### 管理员命令（需要群主、管理员或超级管理员权限）

- `开启舞萌排行榜` - 在当前群开启排行榜功能
- `关闭舞萌排行榜` - 在当前群关闭排行榜功能
- `刷新排行榜` - 手动刷新当前群所有成员的成绩数据
- `加入排行榜 <QQ号>` - 为指定 QQ 号的用户加入排行榜（需要该用户在本群中）
- `退出排行榜 <QQ号>` - 为指定 QQ 号的用户退出排行榜

### 用户命令

- `加入排行榜` 或 `加入排行榜 [QQ号]` - 加入当前群的排行榜
  - 不带参数：自己加入
  - 带 QQ 号：为该 QQ 号加入（需要管理员权限）
- `退出排行榜` 或 `退出排行榜 [QQ号]` - 退出当前群的排行榜
  - 不带参数：自己退出
  - 带 QQ 号：为该 QQ 号退出（需要管理员权限）
- `wmrk <歌曲名/别名/ID>` - 查询该歌曲在本群的排行榜（默认最高难度）
- `wmrk <歌曲名/别名/ID> <难度>` - 查询指定难度的排行榜
  - 难度：`绿`(Basic)、`黄`(Advanced)、`红`(Expert)、`紫`(Master)、`白`(Re:Master)
  - 支持歌曲 ID、完整歌曲名、别名、模糊匹配等多种查询方式

### 使用示例

```
# 用户加入排行榜
用户: 加入排行榜
Bot: ✅ 已成功加入排行榜！
     昵称: 玩家A
     Rating: 14500

# 查询歌曲排行榜（默认最高难度）
用户: wmrk 群青
Bot: [返回群青最高难度(紫)的排行榜图片]

# 查询指定难度
用户: wmrk 群青 红
Bot: [返回群青 Expert 难度的排行榜图片]

# 使用别名查询
用户: wmrk qunqing
Bot: [返回群青的排行榜图片]

# 管理员刷新排行榜
管理: 刷新排行榜
Bot: 正在刷新排行榜数据，请稍候...
Bot: 刷新完成！
     成功: 10 人
     失败: 0 人

# 管理员为他人加入排行榜
管理: 加入排行榜 123456789
Bot: ✅ 已成功为用户 123456789 加入排行榜！
     昵称: 玩家B
     Rating: 13800
```


## 注意事项

1. 用户必须先在[水鱼查分器](https://www.diving-fish.com/maimaidx/prober/)绑定 QQ 号
2. 用户需要在水鱼查分器中关闭隐私设置（允许第三方查询）
3. Developer Token 有请求频率限制，请合理使用
4. 自动更新时间为每天凌晨 0 点（成绩）和 0 点 05 分（别名）
5. 插件会自动创建 `data/maimai_raking` 和 `data/maimai_cache` 目录存储数据
6. 排行榜图片默认显示歌曲的最高难度，可通过参数指定其他难度

## 开源协议

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

## 数据存储

插件会在以下目录存储数据：

- `data/maimai_raking/` - 群组配置和用户数据
  - `groups.json` - 群组配置
  - `users/` - 用户成绩数据
- `data/maimai_cache/` - 缓存数据
  - `alias_data.json` - 别名数据缓存
  - `covers/` - 歌曲封面缓存


## 相关链接

- [NoneBot2 文档](https://nonebot.dev/)
- [水鱼查分器](https://www.diving-fish.com/maimaidx/prober/)
- [舞萌 DX 别名库](https://www.yuzuchan.moe/mai/alias)
