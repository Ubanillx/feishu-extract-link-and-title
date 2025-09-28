import asyncio
from typing import List
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, HttpUrl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# --- Pydantic 模型：用于API数据验证 ---
# 这个类定义了我们API响应列表中每个JSON对象的结构。
class ScrapedLink(BaseModel):
    title: str
    url: str


# --- FastAPI 应用初始化 ---
app = FastAPI(
    title="网页抓取 API",
    description="一个使用 Playwright 从给定 URL 抓取所有链接的 API。",
    version="1.0.0",
)


# --- 异步爬虫函数 ---
# 将您原来的同步函数转换为了异步兼容的版本。

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


# --- API 接口定义 ---
@app.get(
    "/scrape",
    response_model=List[ScrapedLink],
    summary="从URL抓取链接",
    description="提供一个URL，接口将返回页面上找到的所有链接标题及其对应的URL列表。"
)
async def scrape_links_from_url(
        url: HttpUrl = Query(..., description="需要抓取的完整URL。例如: https://www.google.com")
):
    """
    这个接口接收一个URL，运行Playwright爬虫，并返回数据。
    - **url**: 需要被抓取的目标URL，必须是合法的URL格式。
    """
    # Pydantic的HttpUrl类型会自动将查询参数转换为字符串。
    # 我们将这个字符串传递给爬虫函数。
    scraped_data = await crawl_list_page(str(url))
    if not scraped_data:
        raise HTTPException(status_code=404, detail="在页面上没有找到同时包含文本和链接地址的链接。")
    return scraped_data


# 如果直接运行此文件（用于开发），则启动uvicorn服务
if __name__ == "__main__":
    import uvicorn

    # 生产环境建议使用命令行启动: `uvicorn main.api:app --reload`
    uvicorn.run(app, host="0.0.0.0", port=8000)