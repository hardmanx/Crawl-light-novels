# 给 Codex 的项目工作说明

每次修改本项目之前，先读这个文件，再读 `README.md` 和相关代码。

## 项目目标

这个项目是一个本地 Python 小说抓取与 EPUB 生成工具。核心目标是：在不绕过任何访问限制的前提下，从 linovelib.com 的公开页面提取小说目录、章节文字和图片，并按卷生成可检查的 EPUB 及辅助文件。

## 当前代码入口

- 主文件：`linovelib_crawler.py`
- 主类：`LinovelibVolumeEpubCrawler`
- 命令入口：`main()`
- 默认输出目录：`downloads/`

## 修改原则

- 保持改动小而明确，不做无关重构。
- 不要删除用户已下载的内容或 `downloads/` 里的成果。
- 不要改动访问限制相关逻辑去绕过登录、验证码、付费、反爬或权限限制。
- 保持中文注释和用户可见输出，除非用户明确要求改成英文。
- 文件路径要兼容 Windows。
- 处理正文和文件名时，注意中文、空白字符、Windows 非法文件名字符。

## 架构方向

当前最大问题是 `LinovelibVolumeEpubCrawler` 同时承担网络请求、页面解析、正文抽取、图片下载、文件写入、EPUB 生成和流程编排。后续重构时优先考虑下面的深模块方向：

1. 章节内容提取模块
   - 输入：章节 HTML 或 BeautifulSoup 容器、页面 URL。
   - 输出：按原文顺序排列的 blocks。
   - 内部吸收：正文容器选择、无关文字过滤、坏标签过滤、图片地址解析、连续文字合并。

2. 资源获取模块
   - 输入：URL、可选 referer。
   - 输出：文本 HTML 或二进制内容。
   - 内部吸收：请求头、延迟、重试、状态码处理。
   - 未来测试可用本地 fixture adapter，不必访问真实网站。

3. 产物写入模块
   - 输入：book_info、volume_result、输出目录。
   - 输出：生成的 EPUB 和辅助文件路径。
   - 内部吸收：EPUB CSS、目录、spine、图片 item、TXT/MD/JSON 写入规则。

## 验证方式

最轻量检查：

```powershell
python -m py_compile .\linovelib_crawler.py
```

如果改动了解析逻辑，优先加本地 HTML fixture 测试或用很小抓取范围验证：

```powershell
python .\linovelib_crawler.py 2906 --max-volumes 1 --max-chapters 1
```

如果只需要验证指定章节抓取流程，可以使用：

```powershell
python .\linovelib_crawler.py 2906 --volume 1 --chapter 1
```

如果改动了 EPUB 生成，检查 `downloads/小说名/epubs/` 是否生成 EPUB，并确认 `generated_epubs.json` 已更新。

## 已知注意点

- PowerShell 有时会把中文显示成乱码，先确认文件本身编码是否正常，不要只凭终端显示判断文件损坏。
- `DEFAULT_MAX_VOLUMES` 当前适合测试，不代表完整抓取配置。
- 小说名搜索可能触发访问限制，优先使用小说 ID 或详情页链接。
- `downloads/` 可能很大，搜索代码时通常应排除它。

## 推荐搜索命令

```powershell
rg --files -g '!downloads/**' -g '!*.idea/**'
rg -n "def |class |DOWNLOAD_IMAGES|SAVE_TXT|SAVE_MD|DEFAULT_" linovelib_crawler.py
```
