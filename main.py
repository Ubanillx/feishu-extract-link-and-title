import asyncio
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from enum import Enum
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- 枚举和状态管理 ---
class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"

# --- Pydantic 模型：用于API数据验证 ---
class ScrapedLink(BaseModel):
    title: str
    url: str

class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    url: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[List[ScrapedLink]] = None
    error: Optional[str] = None

class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    message: str


# --- 全局变量和配置 ---
# 任务控制配置
MAX_CONCURRENT_TASKS = 1  # 最大并发任务数（设置为1实现串行处理）
TASK_TIMEOUT = 300  # 任务超时时间（秒）
CLEANUP_INTERVAL = 60  # 清理过期任务的间隔（秒）

# 任务存储
task_queue = asyncio.Queue()
active_tasks: Dict[str, TaskInfo] = {}
completed_tasks: Dict[str, TaskInfo] = {}
task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# --- FastAPI 应用初始化 ---
app = FastAPI(
    title="网页抓取 API",
    description="一个使用 Playwright 从给定 URL 抓取所有链接的 API，支持任务队列和串行处理。",
    version="2.0.0",
)


# --- 任务管理函数 ---
async def cleanup_expired_tasks():
    """清理过期的已完成任务"""
    current_time = datetime.now()
    expired_tasks = []
    
    for task_id, task in completed_tasks.items():
        if task.completed_at and (current_time - task.completed_at).seconds > 3600:  # 1小时后清理
            expired_tasks.append(task_id)
    
    for task_id in expired_tasks:
        del completed_tasks[task_id]
        logger.info(f"清理过期任务: {task_id}")

async def task_worker():
    """任务工作器，从队列中获取任务并串行处理"""
    while True:
        try:
            # 等待队列中的任务
            task_info = await task_queue.get()
            task_id = task_info.task_id
            
            # 获取信号量（串行处理，一次只处理一个任务）
            async with task_semaphore:
                try:
                    # 更新任务状态为处理中
                    task_info.status = TaskStatus.PROCESSING
                    task_info.started_at = datetime.now()
                    active_tasks[task_id] = task_info
                    
                    logger.info(f"开始处理任务: {task_id}, URL: {task_info.url}")
                    
                    # 执行爬虫任务
                    result = await crawl_list_page(task_info.url)
                    
                    # 任务完成
                    task_info.status = TaskStatus.COMPLETED
                    task_info.completed_at = datetime.now()
                    task_info.result = [ScrapedLink(**item) for item in result]
                    
                    # 移动到已完成任务
                    completed_tasks[task_id] = task_info
                    if task_id in active_tasks:
                        del active_tasks[task_id]
                    
                    logger.info(f"任务完成: {task_id}, 找到 {len(result)} 个链接")
                    
                except Exception as e:
                    # 任务失败
                    task_info.status = TaskStatus.FAILED
                    task_info.completed_at = datetime.now()
                    task_info.error = str(e)
                    
                    completed_tasks[task_id] = task_info
                    if task_id in active_tasks:
                        del active_tasks[task_id]
                    
                    logger.error(f"任务失败: {task_id}, 错误: {str(e)}")
                
                finally:
                    task_queue.task_done()
                    
        except Exception as e:
            logger.error(f"任务工作器错误: {str(e)}")
            await asyncio.sleep(1)

async def start_background_tasks():
    """启动后台任务"""
    # 启动单个任务工作器（串行处理）
    asyncio.create_task(task_worker())
    
    # 启动清理任务
    async def cleanup_loop():
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            await cleanup_expired_tasks()
    
    asyncio.create_task(cleanup_loop())
    logger.info("启动单个任务工作器（串行处理）和清理任务")

# --- 异步爬虫函数 ---
async def auto_scroll(page, max_steps=15, delay=1):
    """异步滚动页面，以触发懒加载内容。"""
    last_height = await page.evaluate("() => document.body.scrollHeight")
    for _ in range(max_steps):
        await page.mouse.wheel(0, 2000)  # 异步操作需要使用 await
        await asyncio.sleep(delay)  # 在异步函数中使用 asyncio.sleep
        new_height = await page.evaluate("() => document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


async def crawl_list_page(url: str):
    """
    异步启动一个无头浏览器，导航到指定URL，滚动页面，然后抓取所有链接。
    """
    results = []
    async with async_playwright() as p:
        try:
            # 对于服务器环境，headless=True (无头模式) 是最佳选择
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
            page = await context.new_page()

            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_selector("body")

            # 滚动页面以触发懒加载
            await auto_scroll(page, max_steps=12, delay=1)

            # 抓取所有链接
            links = await page.query_selector_all("a")
            for a in links:
                href = await a.get_attribute("href")
                text = (await a.inner_text() or "").strip()
                if href and text:
                    # 将相对 URL 解析为绝对 URL
                    absolute_url = await page.evaluate("(url) => new URL(url, document.baseURI).href", href)
                    results.append({
                        "title": text,
                        "url": absolute_url
                    })

            await browser.close()
        except PlaywrightTimeoutError:
            raise HTTPException(status_code=408, detail=f"访问URL超时: {url}")
        except Exception as e:
            # 捕获其他在抓取过程中可能发生的错误
            raise HTTPException(status_code=500, detail=f"发生意外错误: {str(e)}")

    return results


# --- 应用启动事件 ---
@app.on_event("startup")
async def startup_event():
    """应用启动时初始化后台任务"""
    await start_background_tasks()

# --- API 接口定义 ---
@app.post(
    "/scrape",
    response_model=TaskResponse,
    summary="提交抓取任务",
    description="提交一个URL抓取任务到队列中，返回任务ID用于查询状态。"
)
async def submit_scrape_task(
        url: HttpUrl = Query(..., description="需要抓取的完整URL。例如: https://www.google.com")
):
    """
    提交一个抓取任务到队列中。
    - **url**: 需要被抓取的目标URL，必须是合法的URL格式。
    """
    task_id = str(uuid.uuid4())
    task_info = TaskInfo(
        task_id=task_id,
        status=TaskStatus.PENDING,
        url=str(url),
        created_at=datetime.now()
    )
    
    # 将任务添加到队列
    await task_queue.put(task_info)
    
    logger.info(f"新任务已提交: {task_id}, URL: {url}")
    
    return TaskResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
        message="任务已提交到队列，请使用任务ID查询状态"
    )

@app.get(
    "/task/{task_id}",
    response_model=TaskInfo,
    summary="查询任务状态",
    description="根据任务ID查询任务的当前状态和结果。"
)
async def get_task_status(task_id: str):
    """
    查询指定任务的状态和结果。
    - **task_id**: 任务ID，由提交任务接口返回。
    """
    # 检查活跃任务
    if task_id in active_tasks:
        return active_tasks[task_id]
    
    # 检查已完成任务
    if task_id in completed_tasks:
        return completed_tasks[task_id]
    
    raise HTTPException(status_code=404, detail="任务不存在或已过期")

@app.get(
    "/task/{task_id}/result",
    response_model=List[ScrapedLink],
    summary="获取任务结果",
    description="获取已完成任务的抓取结果。"
)
async def get_task_result(task_id: str):
    """
    获取已完成任务的抓取结果。
    - **task_id**: 任务ID，由提交任务接口返回。
    """
    if task_id not in completed_tasks:
        raise HTTPException(status_code=404, detail="任务不存在或未完成")
    
    task = completed_tasks[task_id]
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail=f"任务状态为 {task.status}，无法获取结果")
    
    if not task.result:
        raise HTTPException(status_code=404, detail="任务结果为空")
    
    return task.result

@app.get(
    "/scrape/sync",
    response_model=List[ScrapedLink],
    summary="同步抓取链接（兼容旧版本）",
    description="提供一个URL，接口将直接返回页面上找到的所有链接标题及其对应的URL列表。注意：此接口会阻塞直到完成。"
)
async def scrape_links_from_url_sync(
        url: HttpUrl = Query(..., description="需要抓取的完整URL。例如: https://www.google.com")
):
    """
    同步抓取接口，保持与旧版本的兼容性。
    - **url**: 需要被抓取的目标URL，必须是合法的URL格式。
    """
    try:
        scraped_data = await crawl_list_page(str(url))
        if not scraped_data:
            raise HTTPException(status_code=404, detail="在页面上没有找到同时包含文本和链接地址的链接。")
        return scraped_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"抓取失败: {str(e)}")

@app.get(
    "/health",
    summary="健康检查",
    description="检查服务状态和队列信息。"
)
async def health_check():
    """健康检查接口"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "queue_size": task_queue.qsize(),
        "active_tasks": len(active_tasks),
        "completed_tasks": len(completed_tasks),
        "max_concurrent_tasks": MAX_CONCURRENT_TASKS
    }


# 如果直接运行此文件（用于开发），则启动uvicorn服务
if __name__ == "__main__":
    import uvicorn

    # 生产环境建议使用命令行启动: `uvicorn main.api:app --reload`
    uvicorn.run(app, host="0.0.0.0", port=8002)