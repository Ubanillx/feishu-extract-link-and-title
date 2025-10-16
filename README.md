# 网页抓取 API - 并发版本

一个使用 FastAPI 和 Playwright 的网页抓取服务，支持并发控制和任务队列管理。

## 功能特性

- ✅ **并发控制**: 支持最多3个并发任务同时执行
- ✅ **任务队列**: 自动排队处理请求，避免系统过载
- ✅ **任务状态跟踪**: 实时监控任务执行状态
- ✅ **异步处理**: 基于异步架构，高性能处理
- ✅ **健康检查**: 提供系统状态监控接口
- ✅ **向后兼容**: 保留同步接口以兼容旧版本

## API 接口

### 1. 提交抓取任务（推荐）

```http
POST /scrape?url=https://example.com
```

**响应:**
```json
{
  "task_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "pending",
  "message": "任务已提交到队列，请使用任务ID查询状态"
}
```

### 2. 查询任务状态

```http
GET /task/{task_id}
```

**响应:**
```json
{
  "task_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "completed",
  "url": "https://example.com",
  "created_at": "2024-01-01T10:00:00",
  "started_at": "2024-01-01T10:00:01",
  "completed_at": "2024-01-01T10:00:30",
  "result": [
    {
      "title": "链接标题",
      "url": "https://example.com/link"
    }
  ],
  "error": null
}
```

### 3. 获取任务结果

```http
GET /task/{task_id}/result
```

**响应:**
```json
[
  {
    "title": "链接标题",
    "url": "https://example.com/link"
  }
]
```

### 4. 同步抓取（兼容旧版本）

```http
GET /scrape/sync?url=https://example.com
```

### 5. 健康检查

```http
GET /health
```

**响应:**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-01T10:00:00",
  "queue_size": 2,
  "active_tasks": 1,
  "completed_tasks": 5,
  "max_concurrent_tasks": 3
}
```

## 任务状态说明

- `pending`: 任务已提交，等待处理
- `processing`: 任务正在执行中
- `completed`: 任务已完成
- `failed`: 任务执行失败
- `timeout`: 任务执行超时

## 配置参数

- `MAX_CONCURRENT_TASKS`: 最大并发任务数（默认：3）
- `TASK_TIMEOUT`: 任务超时时间（默认：300秒）
- `CLEANUP_INTERVAL`: 清理过期任务间隔（默认：60秒）

## 部署方式

### Docker 部署

```bash
# 构建镜像
docker build -t feishu-scraper .

# 运行容器
docker run -p 8002:8002 feishu-scraper
```

### Docker Compose 部署

```bash
docker-compose up -d
```

### 直接运行

```bash
# 安装依赖
pip install -r requirements.txt

# 运行服务
python main.py
```

## 使用示例

### Python 客户端示例

```python
import requests
import time

# 提交任务
response = requests.post("http://localhost:8002/scrape", 
                        params={"url": "https://example.com"})
task_id = response.json()["task_id"]

# 轮询任务状态
while True:
    status_response = requests.get(f"http://localhost:8002/task/{task_id}")
    task_info = status_response.json()
    
    if task_info["status"] == "completed":
        # 获取结果
        result_response = requests.get(f"http://localhost:8002/task/{task_id}/result")
        links = result_response.json()
        print(f"找到 {len(links)} 个链接")
        break
    elif task_info["status"] == "failed":
        print(f"任务失败: {task_info.get('error', '未知错误')}")
        break
    
    time.sleep(1)  # 等待1秒后再次查询
```

### curl 示例

```bash
# 提交任务
TASK_ID=$(curl -s -X POST "http://localhost:8002/scrape?url=https://example.com" | jq -r '.task_id')

# 查询状态
curl -s "http://localhost:8002/task/$TASK_ID" | jq

# 获取结果
curl -s "http://localhost:8002/task/$TASK_ID/result" | jq
```

## 性能优化

1. **并发控制**: 通过信号量限制同时执行的任务数量
2. **队列管理**: 自动排队处理请求，避免系统过载
3. **内存管理**: 定期清理过期的已完成任务
4. **异步处理**: 基于 asyncio 的高性能异步架构

## 监控和日志

服务提供详细的日志记录，包括：
- 任务提交和处理日志
- 错误和异常日志
- 系统状态监控日志

通过 `/health` 接口可以实时监控系统状态。
