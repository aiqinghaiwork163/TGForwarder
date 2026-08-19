# TGForwarder

自动监控 Telegram 频道/群组，将网盘、磁力资源转发到自己的频道；配合链接检测器自动清理失效链接。

效果参考：https://t.me/s/tgsearchers3

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 创建配置

```bash
cp config.yaml.example config.yaml
cp .env.example .env   # 可选，用于覆盖敏感字段
```

编辑 `config.yaml`，填写 `api_id`、`api_hash`、`string_session`、监控频道和目标频道。

**获取凭证：**
- Session（选 V1）：https://tgs.252035.xyz/
- API ID / Hash：https://my.telegram.org/

### 3. 运行

```bash
# 转发资源（推荐）
python main.py forward

# 检测失效网盘链接（仅标记，不删除）
python main.py check-links

# 检测并删除失效链接
python main.py check-links --mode 1

# 尝试加入监控频道
python main.py join

# 清理指定时间段消息
python main.py clear --start "2025-01-08 23:55" --end "2025-01-09 08:00"
```

### 4. Docker 部署

```bash
cp config.yaml.example config.yaml
# 填写 config.yaml 或设置 .env 环境变量

docker compose up -d forwarder          # 持续转发
docker compose --profile checker run link-checker  # 一次性链接检测
```

### 5. 定时任务（cron 示例）

```cron
# 每 10 分钟转发一次
*/10 * * * * cd /path/to/TGForwarder && python main.py forward >> logs/forward.log 2>&1

# 每天凌晨 3 点检测失效链接
0 3 * * * cd /path/to/TGForwarder && python main.py check-links >> logs/check.log 2>&1
```

---

## 主要功能

### 资源转发（TGForwarder）

- 突破频道禁止转发限制
- **增量监听**：记录每个源频道的 `last_message_id`，只处理新消息（`monitor.incremental: true`）
- **链接规范化去重**：同一分享链接带不同参数不会重复转发
- 支持关键词过滤（包含/排除）、超链接还原、Telegraph 解析、Bot `/start` 取链
- 支持评论监控、多频道分发、自定义替换与置顶统计
- 自动清理重复历史消息

### 链接检测（TGNetDiskLinkChecker）

支持夸克、天翼、阿里云、115、123、百度、UC 等网盘。

- `delete_mode`：`1` 检测并删除 / `2` 仅检测标记 / `3` 删除已标记失效
- 二次复检防止误判；123 网盘单独存储、谨慎删除
- 请求失败视为有效，避免误删

> ⚠️ 检测删除有风险，请先用 `--mode 2` 观察后再开启删除。

---

## 配置说明

完整配置见 [`config.yaml.example`](config.yaml.example)。

| 配置段 | 说明 |
|--------|------|
| `telegram` | API 凭证、目标频道 |
| `monitor.channels` | 监控列表，支持 `频道\|20`、`频道\|reply_1` 语法 |
| `monitor.incremental` | 增量监听（推荐开启） |
| `filters` | 包含/排除关键词 |
| `link_checker` | 链接检测参数 |
| `proxy` | SOCKS5/HTTP 代理 |

环境变量可覆盖敏感字段：`API_ID`、`API_HASH`、`STRING_SESSION`、`FORWARD_TO_CHANNEL`。

---

## 项目结构

```
TGForwarder/
├── main.py                  # 统一 CLI 入口
├── TGForwarder.py           # 转发核心
├── TGNetDiskLinkChecker.py  # 链接检测
├── utils/
│   ├── link_utils.py        # 链接提取与规范化
│   ├── config_loader.py     # YAML + 环境变量加载
│   └── logging_setup.py     # 统一日志
├── config.yaml.example
├── Dockerfile
└── tests/
```

---

## 开发

```bash
python -m unittest discover -s tests -v
```

---

## 更新日志（近期）

- 配置外置（YAML + 环境变量）
- 统一 CLI（`main.py forward / check-links / join / clear`）
- 增量监听 + 链接规范化去重
- 修复 `redirect_url` 返回值、`hyperlink_text` 变量引用等 Bug
- 结构化日志、Docker 部署支持
