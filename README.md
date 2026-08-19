# TGForwarder

自动监控 Telegram 频道/群组，将网盘、磁力资源转发到自己的频道；配合链接检测器自动清理失效链接。

效果参考：https://t.me/s/tgsearchers3

## 快速开始

```bash
pip install -r requirements.txt
cp config.yaml.example config.yaml
cp .env.example .env          # 可选
# 编辑 config.yaml 填写 API 凭证与频道列表
python main.py forward
```

**获取凭证：**
- Session（V1）：https://tgs.252035.xyz/
- API ID / Hash：https://my.telegram.org/
- TMDB Key（可选）：https://www.themoviedb.org/settings/api

## 命令一览

| 命令 | 说明 |
|------|------|
| `python main.py forward` | 监控源频道并转发资源 |
| `python main.py check-links` | 检测失效网盘链接 |
| `python main.py check-links --mode 4` | 检测并在消息中编辑标记失效链接（推荐） |
| `python main.py run-all` | 转发 + 检测完整流水线 |
| `python main.py bot` | 启动 Telegram 管理 Bot |
| `python main.py search 关键词` | 搜索已索引资源 |
| `python main.py join` | 尝试加入监控频道 |
| `python main.py clear --start ... --end ...` | 清理指定时间段消息 |

### 链接检测模式

| mode | 行为 |
|------|------|
| 1 | 检测并**删除**失效消息 |
| 2 | 仅检测并标记（不修改消息） |
| 3 | 删除已标记为失效的消息 |
| 4 | 检测并**编辑**消息，将失效 URL 替换为 `[已失效]`（推荐） |

## 管理 Bot

在 `config.yaml` 中配置管理员 ID 后启动：

```yaml
admin:
  user_ids: [123456789]          # 你的 Telegram User ID
  schedule_interval_minutes: 10  # 每 10 分钟自动 run-all（0=关闭）
```

```bash
python main.py bot
```

向自己的 Telegram 账号（或配置的 chat）发送命令：

- `/status` — 运行状态与统计
- `/forward` — 手动转发
- `/check` — 手动链接检测
- `/run` — 完整流水线
- `/search 沙丘` — 搜索资源
- `/pause` / `/resume` — 暂停/恢复定时任务

## 主要功能

### 资源转发
- 增量监听（只处理新消息）
- 链接规范化去重
- TMDB 元数据自动补全（可选）
- 关键词过滤、超链接还原、评论监控、多频道分发

### 链接检测
- SQLite 持久化（自动从 JSON 迁移）
- 二次复检防误判
- 编辑模式保留消息内容

### 搜索索引
- 转发资源自动写入 SQLite
- CLI / Bot 均可搜索

## Docker

```bash
docker compose up -d forwarder                              # 持续转发
docker compose --profile checker run link-checker           # 链接检测
docker compose --profile bot up -d bot                      # 管理 Bot
```

## 定时任务

```cron
*/10 * * * * cd /path/to/TGForwarder && python main.py run-all >> logs/run.log 2>&1
```

## 项目结构

```
TGForwarder/
├── main.py                  # 统一 CLI
├── TGForwarder.py           # 转发核心
├── TGNetDiskLinkChecker.py  # 链接检测
├── TGAdminBot.py            # 管理 Bot
├── utils/
│   ├── database.py          # SQLite 持久化
│   ├── link_utils.py        # 链接提取与规范化
│   ├── tmdb.py              # TMDB 元数据
│   └── config_loader.py
├── config.yaml.example
└── data/tgforwarder.db      # 运行时数据库
```

## 开发

```bash
python3 -m unittest discover -s tests -v
```
