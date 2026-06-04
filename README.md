# 小说 EPUB 爬取项目

这是一个用于从 linovelib.com 公开可访问页面抓取小说内容，并按卷生成 EPUB 的本地 Python 项目。当前核心程序是 `linovelib_crawler.py`。

## 项目内容

- `linovelib_crawler.py`：主程序，负责解析小说信息、目录、章节正文、图片，并生成 EPUB。
- `linovelib_gui.py`：本地可视化 App，可以加载小说目录、选择卷章、启动/停止爬取、查看实时日志。
- `run_gui.bat`：Windows 双击启动可视化界面的脚本。
- `downloads/`：运行后保存小说内容、图片、EPUB 和检查文件的目录。
- `.idea/`：本地 IDE 配置目录。

## 当前功能

- 支持输入小说 ID、小说详情页链接、章节页链接，或小说名。
- 支持传统分卷页 `vol_xxx.html`。
- 支持 `catalog` 目录页，并尝试按目录标题拆分卷。
- 按网页原文顺序提取文字和图片。
- 部分章节会优先按本机 Edge / Chrome 渲染后的可见正文提取，过滤网页隐藏的诱饵段落，避免 EPUB 正文顺序错乱。
- 每一卷单独生成一个 EPUB。
- 用户端默认只输出 EPUB 文件；调试用 TXT、MD、JSON 输出默认关闭。
- 对请求设置延迟和重试，避免过快访问。

## 运行方式

先安装依赖：

```powershell
pip install requests beautifulsoup4 ebooklib tqdm
```

运行默认配置：

```powershell
python .\linovelib_crawler.py
```

启动可视化界面：

```powershell
python .\linovelib_gui.py
```

也可以在资源管理器里双击：

```text
run_gui.bat
```

生成 Windows 安装包：

```powershell
winget install --id JRSoftware.InnoSetup --exact
.\build_installer.bat
```

生成完成后，安装程序会保存到：

```text
release/LightNovelEPUBWorkbench_Setup_v1.0.0.exe
```

用户拿到这个安装程序后，按提示安装即可直接打开 `轻小说 EPUB 工作台` 使用，不需要另外安装 Python 或项目依赖。

可视化界面完整流程：

1. 填写小说 ID 或小说名称。
2. 设置 `请求间隔秒`。默认 1.5 秒，遇到 `403` 或 `429` 时调高。
3. 选择本地电脑上的 `下载目录`。
4. 如需插图，勾选 `下载并嵌入图片`。不勾选会更快。
5. 如需网页提示“注意有剧透”的完整插图，再勾选 `包含剧透完整插图`。
6. 点击 `加载小说目录`。
7. 在中间的卷章列表里单击卷或章节进行勾选。
8. 点击 `开始爬取所选章节`。
9. 在右侧查看实时日志，完成后点击 `打开下载目录` 查看 EPUB。

指定小说 ID 或链接：

```powershell
python .\linovelib_crawler.py 2906
python .\linovelib_crawler.py https://www.linovelib.com/novel/2906.html
```

限制抓取范围，适合测试：

```powershell
python .\linovelib_crawler.py 2906 --max-volumes 1 --max-chapters 2
```

只抓取指定卷的指定章节：

```powershell
python .\linovelib_crawler.py 2906 --volume 3 --chapter 5
```

上面的命令表示只抓取第 3 卷第 5 章。卷号和章节号都从 1 开始。指定 `--chapter` 时必须同时指定 `--volume`。

## 主要配置

这些配置目前在 `linovelib_crawler.py` 顶部：

- `NOVEL_KEYWORD`：默认小说 ID、小说名或链接。
- `DEFAULT_MAX_VOLUMES`：默认最多抓取几卷。
- `DEFAULT_MAX_CHAPTERS`：每卷默认最多抓取几章。
- `DEFAULT_DELAY`：每次请求前的等待秒数。
- `DOWNLOAD_IMAGES`：是否下载并嵌入图片。
- `INCLUDE_SPOILER_IMAGES`：是否包含网页隐藏的完整插图，默认关闭。
- `SAVE_TXT`：是否保存 TXT，默认关闭。
- `SAVE_MD`：是否保存每章 MD，默认关闭。
- `SAVE_JSON`：是否保存调试 JSON，默认关闭。

命令行也支持：

- `--volume N`：只爬取第 N 卷。
- `--chapter N`：只爬取指定卷中的第 N 章，需要和 `--volume` 一起使用。
- `--include-spoiler-images`：包含网页隐藏的完整插图。该区域通常提示有剧透，默认不包含。

可视化界面里有对应操作：

- `小说ID / 名称`
- `请求间隔秒`
- `搜索页数`
- `下载目录`
- `☐/☑ 下载并嵌入图片`
- `☐/☑ 包含剧透完整插图`
- `加载小说目录`
- `卷章选择`
- `开始爬取所选章节`

只抓单章时，加载目录后在中间列表里单击对应章节即可。

## 输出位置

命令行默认输出到项目目录下：

```text
downloads/小说名/
```

可视化界面可以自行选择本地电脑上的下载目录。
界面会自适应窗口大小；窗口高度较小时，左侧设置区可以滚动。

常见输出包括：

- `epubs/`：生成的 EPUB。

图片下载会使用临时目录参与 EPUB 打包，生成完成后会自动清理。TXT、Markdown、JSON 等调试文件默认不会输出。

## 使用注意

本项目只应保存公开可访问且你有权保存的内容。不要绕过登录、验证码、付费、反爬或访问限制。

如果遇到 `403` 或 `429`，通常表示访问受限或请求太频繁。建议降低抓取速度、减少扫描页数，或稍后再试。

## 当前架构状态

当前项目主要集中在一个较大的脚本里。后续最值得优先改进的是“章节内容提取”部分：把正文容器查找、文字过滤、图片地址提取、DOM 顺序遍历集中成一个更深的模块，这样更容易测试，也更容易适配网页结构变化。
