# 小说 EPUB 爬取项目

这是一个用于从 linovelib.com 公开可访问页面抓取小说内容，并按卷生成 EPUB 的本地 Python 项目。当前核心程序是 `linovelib_crawler.py`。

## 项目内容

- `linovelib_crawler.py`：主程序，负责解析小说信息、目录、章节正文、图片，并生成 EPUB、TXT、MD、JSON 辅助文件。
- `downloads/`：运行后保存小说内容、图片、EPUB 和检查文件的目录。
- `.idea/`：本地 IDE 配置目录。

## 当前功能

- 支持输入小说 ID、小说详情页链接、章节页链接，或小说名。
- 支持传统分卷页 `vol_xxx.html`。
- 支持 `catalog` 目录页，并尝试按目录标题拆分卷。
- 按网页原文顺序提取文字和图片。
- 每一卷单独生成一个 EPUB。
- 同时保存 TXT、每章 MD、章节 JSON、书籍 JSON，便于检查。
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
- `SAVE_TXT`：是否保存 TXT。
- `SAVE_MD`：是否保存每章 MD。

命令行也支持：

- `--volume N`：只爬取第 N 卷。
- `--chapter N`：只爬取指定卷中的第 N 章，需要和 `--volume` 一起使用。

## 输出位置

默认输出到：

```text
downloads/小说名/
```

常见输出包括：

- `epubs/`：生成的 EPUB。
- `epub_images/`：下载的图片。
- `book_info.json`：书籍信息。
- `generated_epubs.json`：已生成 EPUB 路径。
- `01_卷名/`、`02_卷名/`：每卷的章节、TXT、MD、JSON。

## 使用注意

本项目只应保存公开可访问且你有权保存的内容。不要绕过登录、验证码、付费、反爬或访问限制。

如果遇到 `403` 或 `429`，通常表示访问受限或请求太频繁。建议降低抓取速度、减少扫描页数，或稍后再试。

## 当前架构状态

当前项目主要集中在一个较大的脚本里。后续最值得优先改进的是“章节内容提取”部分：把正文容器查找、文字过滤、图片地址提取、DOM 顺序遍历集中成一个更深的模块，这样更容易测试，也更容易适配网页结构变化。
