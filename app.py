from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel
from typing import Optional, List, Any

from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

import os
import logging
import threading
import uuid
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期事件处理"""
    # 启动时执行
    try:
        init_driver()
        logger.info("应用启动时自动初始化浏览器成功")
    except Exception as e:
        logger.error(f"应用启动时初始化浏览器失败: {str(e)}")
    
    yield
    
    # 关闭时执行
    global driver
    with driver_lock:
        if driver is not None:
            driver.quit()
            driver = None
            logger.info("应用关闭时自动退出浏览器成功")

app = FastAPI(
    title="Firefox Browser API",
    description="基于Windows容器的Firefox浏览器API服务",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

driver = None
driver_lock = threading.Lock()
templates = Jinja2Templates(directory="templates")

def get_driver():
    """获取driver实例，如果未启动则抛出异常"""
    global driver
    if driver is None:
        raise HTTPException(status_code=400, detail="浏览器未启动")
    return driver

def format_url(url):
    """确保URL包含协议前缀"""
    if not url.startswith(('http://', 'https://')):
        return 'https://' + url
    return url

def with_browser(func):
    """装饰器：确保浏览器已启动并处理异常"""
    async def wrapper(*args, **kwargs):
        with driver_lock:
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"操作失败: {str(e)}")
    return wrapper

class URLRequest(BaseModel):
    url: str

class TabRequest(BaseModel):
    handle: Optional[str] = None
    url: Optional[str] = "about:blank"

class ScriptRequest(BaseModel):
    script: str

class TabInfo(BaseModel):
    handle: str
    title: str
    url: str
    is_current: bool

class TabListResponse(BaseModel):
    tabs: List[TabInfo]
    count: int

class StatusResponse(BaseModel):
    browser_running: bool
    title: Optional[str] = None
    url: Optional[str] = None
    window_handles: Optional[List[str]] = None
    current_window_handle: Optional[str] = None
    tabs_count: Optional[int] = None

class BasicResponse(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None

class TabOperationResponse(BasicResponse):
    window_handle: Optional[str] = None
    remaining_tabs: Optional[int] = None

class ScriptResponse(BaseModel):
    result: Optional[Any] = None

def init_driver():
    """初始化Firefox浏览器驱动"""
    global driver
    with driver_lock:
        if driver is None:
            try:
                # 清理可能残留的驱动进程
                import subprocess
                try:
                    # 在Windows上结束可能的残留geckodriver进程
                    subprocess.run(['taskkill', '/F', '/IM', 'geckodriver.exe'],
                                 capture_output=True, text=True, timeout=5)
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass  # 忽略超时或命令不存在错误
                
                firefox_options = Options()
                firefox_options.add_argument("--headless")
                firefox_options.add_argument("--no-sandbox")
                firefox_options.add_argument("--disable-dev-shm-usage")
                firefox_options.add_argument("--window-size=2560,1440")
                firefox_options.add_argument("--font-render-hinting=none")
                firefox_options.add_argument("--disable-gpu")
                # 添加更多稳定性参数
                firefox_options.add_argument("--disable-extensions")
                firefox_options.add_argument("--disable-infobars")
                firefox_options.add_argument("--disable-notifications")
                firefox_options.add_argument("--disable-popup-blocking")
                
                # 尝试初始化驱动
                service = Service(GeckoDriverManager().install())
                driver = webdriver.Firefox(service=service, options=firefox_options)
                
                # 设置超时时间
                driver.set_page_load_timeout(30)
                driver.set_script_timeout(30)
                
                driver.set_window_size(2560, 1440)
                
                # 导航到默认页面
                driver.get("https://www.bing.com/")
                
                # 等待页面加载
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC
                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
                
                logger.info("Firefox浏览器初始化成功")
            except Exception as e:
                logger.error(f"初始化Firefox浏览器失败: {str(e)}")
                # 确保在失败时清理driver
                if driver is not None:
                    try:
                        driver.quit()
                    except:
                        pass
                    driver = None
                raise


@app.get("/api/screenshot", summary="截取完整页面截图", tags=["浏览器控制"])
async def take_screenshot():
    """截取完整页面截图并返回PNG格式图片"""
    global driver
    if driver is None:
        raise HTTPException(
            status_code=422,
            detail="浏览器未启动"
        )
    
    with driver_lock:
        try:
            # 检查浏览器连接状态
            try:
                # 简单检查浏览器是否响应
                _ = driver.current_url
            except Exception as e:
                logger.error(f"浏览器连接失败: {str(e)}")
                raise HTTPException(status_code=500, detail="浏览器连接失败，请重启浏览器")
            
            # 等待页面加载，使用更短的超时时间
            wait_for_page_load_safely(driver)
            
            # 尝试截图，先尝试简单截图，如果失败则尝试完整页面截图
            try:
                screenshot = driver.get_screenshot_as_png()
                logger.info("简单截图成功")
            except Exception as e:
                logger.warning(f"简单截图失败，尝试完整页面截图: {str(e)}")
                screenshot = take_full_page_screenshot_safely(driver)
            
            temp_dir = "temp"
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir)
            
            filename = f"{temp_dir}/screenshot_{uuid.uuid4()}.png"
            with open(filename, 'wb') as f:
                f.write(screenshot)
            
            return FileResponse(filename, media_type="image/png", filename="screenshot.png")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"截图失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"截图失败: {str(e)}")

def wait_for_page_load(driver, timeout=30):
    """等待页面完全加载"""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        
        time.sleep(2)
        
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script(
                "return Array.from(document.images).every(img => img.complete)"
            )
        )
        
        logger.info("页面加载完成")
    except TimeoutException:
        logger.warning("等待页面加载超时，继续截图")
    except Exception as e:
        logger.warning(f"等待页面加载时发生错误: {str(e)}")

def wait_for_page_load_safely(driver, timeout=15):
    """安全地等待页面加载，避免因连接问题导致截图失败"""
    try:
        # 只等待页面基本加载完成，不等待图片
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        
        # 短暂等待，确保页面稳定
        time.sleep(1)
        
        logger.info("页面基本加载完成")
    except TimeoutException:
        logger.warning("等待页面加载超时，继续截图")
    except Exception as e:
        logger.warning(f"等待页面加载时发生错误: {str(e)}")

def take_full_page_screenshot(driver):
    """截取完整页面截图"""
    try:
        total_width = driver.execute_script("return document.body.scrollWidth")
        total_height = driver.execute_script("return document.body.scrollHeight")
        
        current_width = driver.execute_script("return window.innerWidth")
        current_height = driver.execute_script("return window.innerHeight")
        
        driver.set_window_size(total_width, total_height)
        time.sleep(1)
        
        screenshot = driver.get_screenshot_as_png()
        
        driver.set_window_size(current_width, current_height)
        
        return screenshot
    except Exception as e:
        logger.error(f"完整页面截图失败: {str(e)}")
        return driver.get_screenshot_as_png()

def take_full_page_screenshot_safely(driver):
    """安全地截取完整页面截图，避免因JavaScript执行失败导致截图空白"""
    try:
        # 先尝试获取当前窗口截图作为备用
        backup_screenshot = driver.get_screenshot_as_png()
        
        # 尝试获取页面尺寸
        try:
            total_width = driver.execute_script("return document.body.scrollWidth")
            total_height = driver.execute_script("return document.body.scrollHeight")
            
            # 如果获取的尺寸不合理，使用默认尺寸
            if total_width <= 0 or total_height <= 0:
                logger.warning("获取的页面尺寸不合理，使用默认尺寸")
                total_width = 1920
                total_height = 1080
            
            current_width = driver.execute_script("return window.innerWidth")
            current_height = driver.execute_script("return window.innerHeight")
            
            # 保存当前窗口尺寸
            original_size = driver.get_window_size()
            
            # 设置窗口尺寸以适应整个页面
            try:
                driver.set_window_size(total_width, total_height)
                time.sleep(1)  # 等待窗口调整完成
                
                # 尝试截图
                screenshot = driver.get_screenshot_as_png()
                
                # 恢复原始窗口尺寸
                driver.set_window_size(original_size['width'], original_size['height'])
                
                return screenshot
            except Exception as e:
                logger.warning(f"调整窗口尺寸失败: {str(e)}")
                # 恢复原始窗口尺寸
                try:
                    driver.set_window_size(original_size['width'], original_size['height'])
                except:
                    pass
                return backup_screenshot
        except Exception as e:
            logger.warning(f"获取页面尺寸失败: {str(e)}")
            return backup_screenshot
    except Exception as e:
        logger.error(f"安全截图失败: {str(e)}")
        # 最后的备用方案：返回当前窗口截图
        try:
            return driver.get_screenshot_as_png()
        except Exception as final_error:
            logger.error(f"最终截图方案也失败: {str(final_error)}")
            raise HTTPException(status_code=500, detail="截图功能完全失败，请重启浏览器")

@app.post("/api/navigate", response_model=BasicResponse, summary="导航到指定URL", tags=["浏览器控制"])
async def navigate_to_url(request: URLRequest):
    """导航到指定的URL"""
    global driver
    if driver is None:
        raise HTTPException(
            status_code=422,
            detail="浏览器未启动"
        )
    
    with driver_lock:
        try:
            url = format_url(request.url)
            
            # 导航到指定URL
            driver.get(url)
            
            # 等待页面加载完成
            wait_for_page_load(driver)
            
            # 返回页面标题和URL
            return {
                "title": driver.title,
                "url": driver.current_url
            }
        except TimeoutException:
            # 页面加载超时，但仍返回当前状态
            logger.warning(f"导航到 {url} 超时")
            return {
                "title": driver.title,
                "url": driver.current_url
            }
        except Exception as e:
            logger.error(f"导航到 {url} 失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"导航失败: {str(e)}")

@app.post("/api/open_tab", response_model=TabOperationResponse, summary="打开新标签页", tags=["标签页管理"])
async def open_new_tab(request: TabRequest):
    """打开新标签页，可选择导航到指定URL"""
    global driver
    if driver is None:
        raise HTTPException(
            status_code=422,
            detail="浏览器未启动"
        )
    
    with driver_lock:
        try:
            driver.execute_script("window.open('');")
            
            driver.switch_to.window(driver.window_handles[-1])
            url = request.url or "about:blank"
            if url and url != 'about:blank':
                url = format_url(url)
                driver.get(url)
                # 等待页面加载完成
                wait_for_page_load(driver)
            
            return {
                "url": url,
                "title": driver.title,
                "window_handle": driver.current_window_handle
            }
        except Exception as e:
            logger.error(f"打开新标签页失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"打开新标签页失败: {str(e)}")

@app.get("/api/tabs", response_model=TabListResponse, summary="列出所有标签页", tags=["标签页管理"])
async def list_tabs():
    """列出所有标签页"""
    global driver
    if driver is None:
        raise HTTPException(
            status_code=422,
            detail="浏览器未启动"
        )
    
    with driver_lock:
        try:
            tabs = []
            current_handle = driver.current_window_handle
            
            for handle in driver.window_handles:
                driver.switch_to.window(handle)
                tabs.append({
                    "handle": handle,
                    "title": driver.title,
                    "url": driver.current_url,
                    "is_current": handle == current_handle
                })
            
            driver.switch_to.window(current_handle)
            
            return {
                "tabs": tabs,
                "count": len(tabs)
            }
        except Exception as e:
            logger.error(f"获取标签页列表失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"获取标签页列表失败: {str(e)}")

@app.post("/api/switch_tab", response_model=BasicResponse, summary="切换到指定标签页", tags=["标签页管理"])
async def switch_tab(request: TabRequest):
    """切换到指定标签页"""
    if not request.handle:
        raise HTTPException(status_code=400, detail="缺少标签页句柄参数")
    
    global driver
    if driver is None:
        raise HTTPException(
            status_code=422,
            detail="浏览器未启动"
        )
    
    with driver_lock:
        try:
            if request.handle not in driver.window_handles:
                raise HTTPException(status_code=404, detail="指定的标签页不存在")
            
            driver.switch_to.window(request.handle)
            
            return {
                "title": driver.title,
                "url": driver.current_url
            }
        except Exception as e:
            logger.error(f"切换标签页失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"切换标签页失败: {str(e)}")

@app.post("/api/close_tab", response_model=TabOperationResponse, summary="关闭标签页", tags=["标签页管理"])
async def close_tab(request: TabRequest):
    """关闭当前标签页或指定标签页"""
    global driver
    if driver is None:
        raise HTTPException(
            status_code=422,
            detail="浏览器未启动"
        )
    
    with driver_lock:
        try:
            tab_handle = request.handle or driver.current_window_handle
            
            if tab_handle not in driver.window_handles:
                raise HTTPException(status_code=404, detail="指定的标签页不存在")
            
            if len(driver.window_handles) == 1:
                raise HTTPException(status_code=400, detail="不能关闭最后一个标签页")
            
            driver.switch_to.window(tab_handle)
            driver.close()
            
            driver.switch_to.window(driver.window_handles[0])
            
            return {
                "remaining_tabs": len(driver.window_handles)
            }
        except Exception as e:
            logger.error(f"关闭标签页失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"关闭标签页失败: {str(e)}")

@app.post("/api/execute_script", response_model=ScriptResponse, summary="执行JavaScript代码", tags=["浏览器控制"])
async def execute_script(request: ScriptRequest):
    """在当前页面执行JavaScript代码"""
    if not request.script:
        raise HTTPException(
            status_code=400,
            detail="缺少JavaScript代码",
            headers={"Content-Type": "application/json"}
        )
    
    global driver
    if driver is None:
        raise HTTPException(
            status_code=422,
            detail="浏览器未启动",
            headers={"Content-Type": "application/json"}
        )
    
    with driver_lock:
        try:
            # 执行JavaScript代码
            result = driver.execute_script(request.script)
            
            # 尝试序列化结果，确保可以转换为JSON
            try:
                # 如果结果是None，直接返回
                if result is None:
                    return {"result": None}
                
                # 如果结果是基本类型，直接返回
                if isinstance(result, (str, int, float, bool)):
                    return {"result": result}
                
                # 如果结果是字典或列表，确保所有元素都可以序列化
                if isinstance(result, (dict, list)):
                    # 使用自定义的JSON序列化器
                    import json
                    serialized_result = json.loads(json.dumps(result, default=str))
                    return {"result": serialized_result}
                
                # 其他类型转换为字符串
                return {"result": str(result)}
                
            except Exception as serialization_error:
                logger.error(f"序列化JavaScript执行结果失败: {str(serialization_error)}")
                # 如果序列化失败，尝试将结果转换为字符串
                try:
                    return {"result": str(result)}
                except Exception as str_conversion_error:
                    logger.error(f"将结果转换为字符串也失败: {str(str_conversion_error)}")
                    return {"result": f"[无法序列化的结果类型: {type(result).__name__}]"}
                    
        except Exception as e:
            logger.error(f"执行JavaScript代码失败: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"执行JavaScript代码失败: {str(e)}",
                headers={"Content-Type": "application/json"}
            )

@app.get("/api/status", response_model=StatusResponse, summary="获取浏览器状态", tags=["浏览器控制"])
async def get_status():
    """获取当前浏览器状态信息"""
    with driver_lock:
        if driver is None:
            return {
                "browser_running": False
            }
        
        return {
            "browser_running": True,
            "title": driver.title,
            "url": driver.current_url,
            "window_handles": driver.window_handles,
            "current_window_handle": driver.current_window_handle,
            "tabs_count": len(driver.window_handles)
        }


@app.get("/", summary="浏览器控制台主页", tags=["主页"])
async def index(request: Request):
    """返回浏览器控制台主页"""
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == '__main__':
    if not os.path.exists("temp"):
        os.makedirs("temp")
    
    import uvicorn
    uvicorn.run("app:app", host='0.0.0.0', port=5000, reload=True)