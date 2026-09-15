# 普通模式独立版

双击启动脚本即可使用，所有路径自动推导，可在任意电脑上直接运行：

- **Mac**：双击 `启动普通模式.command`
- **Windows**：双击 `启动普通模式.bat`
- **Linux**：`.command` 无法双击，在终端里执行 `bash 启动普通模式.command`（或使用下方命令行方式）

首次双击会自动完成：创建虚拟环境 → 安装依赖（需联网，几分钟）→ 跳过 Streamlit 首次运行的邮箱询问 → 打开浏览器。默认地址 `http://localhost:8502`（端口被占用时自动换下一个可用端口），不会占用原双模式软件的 8501 端口。

## 系统要求（哪些电脑打不开）

| 项目 | 最低要求 | 推荐 |
|---|---|---|
| macOS | 10.15 + Python 3.10 | macOS 12 及以上 + Python 3.12+ |
| Windows | Windows 10 64 位 + Python 3.10 | Windows 11 64 位 + Python 3.12+ |
| Linux | Python 3.10 | Python 3.12+ |

**无法运行的情况**：

- 32 位 Windows、Windows 7/8、macOS 10.14 及更老系统（核心依赖 pyarrow/numpy 没有对应版本）。
- Python 3.9 及以下（streamlit 1.59 要求 Python 3.10 起）。
- Python 低于 3.12 时仍可运行：脚本会自动改用 `requirements.txt` 安装兼容版本（功能一致，依赖版本与锁定清单略有差异）。
- 完全离线的电脑首次无法启动（需要联网装依赖）；装好之后不再需要联网。

## Mac 打开流程（新电脑从零开始）

1. **安装 Python**（装过可跳过）：打开 <https://www.python.org/downloads/>，下载 3.12 或更新版本的 macOS 安装包，双击安装，一路默认即可。不要依赖系统自带的 python3（多数是 3.9 老版本）。
2. **双击 `启动普通模式.command`**。
3. **如果被系统拦截**（提示"无法打开"或"无法验证开发者"——通过微信/AirDrop/网盘/浏览器下载拷来的文件都会这样，因为不是 App Store 认证的开发者签名），任选一种放行方式：
   - **右键方式（最简单）**：右键点击 `启动普通模式.command` → **打开** → 弹窗里再点一次 **打开**。每个新拷贝的文件只需做一次。
   - **系统设置方式**：打开 **系统设置 → 隐私与安全性**，往下滚动找到 "已阻止将…用于…"/"已阻止使用" 的提示，点 **仍要打开**。
   - **终端方式**：执行 `xattr -dr com.apple.quarantine /项目路径/DND`。
4. **如果弹出"终端"想访问"桌面"/"文稿"文件夹** → 点 **允许/好**。如果之前点过"不允许"，去 **系统设置 → 隐私与安全性 → 文件和文件夹 → 终端**，勾选"桌面文件夹"/"文稿文件夹"；或者在 **隐私与安全性 → 完全磁盘访问权限** 里直接打开"终端"的开关。
5. 等待依赖安装完成（终端会显示进度），浏览器自动打开 `http://localhost:8502`。

**Mac 常见问题**：

- 提示"找不到 python3"或"Python 版本过低（需要 3.10+）"：按第 1 步安装 python.org 新版后重新双击。
- 双击后没反应或提示"权限不足"：文件拷贝时丢失了执行权限，终端执行 `chmod +x /项目路径/启动普通模式.command` 后再双击。
- 文件夹不要直接放在 U 盘/exFAT 移动硬盘里双击（这类盘无法保存执行权限），先拷贝到本机磁盘（桌面/文稿均可）再启动。
- 建议不要放在开启了 iCloud"优化储存"的同步目录里，避免数据文件被云端化后读取变慢或出错。
- 杀毒软件（如 Little Snitch、Lulu）拦截终端联网装依赖时，选择允许。

## Windows 打开流程（新电脑从零开始）

1. **安装 Python**（装过可跳过）：打开 <https://www.python.org/downloads/>，下载 3.12 或更新版本的安装包，双击运行，**第一屏底部务必勾选 "Add python.exe to PATH"**，然后点 Install Now。
2. **双击 `启动普通模式.bat`**。
3. **如果 SmartScreen 拦截**（蓝色弹窗"Windows 已保护你的电脑"）：点 **更多信息** → **仍要运行**。也可以右键文件 → 属性 → 底部勾选 **解除锁定** → 确定。
4. **如果杀毒软件报毒/拦截**：选择"允许"或将整个文件夹加入信任区（批处理脚本和 pip 安装常被误报）。
5. 等待依赖安装完成（窗口会显示进度），浏览器自动打开 `http://localhost:8502`。

**Windows 常见问题**：

- **输入 python 却弹出 Microsoft Store**：说明只有商店占位符、没装真 Python。按第 1 步安装 python.org 版本即可；也可以在 **设置 → 应用 → 高级应用设置 → 应用执行别名** 里关闭 python.exe 的两个开关。
- **提示找不到 Python / py 不是内部或外部命令**：安装时没勾选 "Add python.exe to PATH"。重新运行安装包，选 Modify，勾上该项即可。
- **提示"Python 版本过低（需要 3.10+）"**：电脑上有旧版 Python（如 3.8）。安装 python.org 新版（默认会排在 PATH 前面）后重新双击；若仍识别到旧版，可在"应用"里卸载旧版。
- 文件夹路径里不要包含 `!` 或 `%` 字符，个别情况下批处理解析会异常。
- 窗口一闪而过看不到报错：右键 `启动普通模式.bat` → 编辑看不到的话，先开一个 CMD 窗口，把 bat 拖进去按回车运行，报错会停留在窗口里。

## 命令行手动启动（不依赖双击脚本）

把下面的"项目目录"换成你电脑上本文件夹的实际路径即可。启动参数里已包含跳过邮箱询问和关闭匿名统计的选项。

### Mac / Linux（终端）

首次使用（虚拟环境不存在时）先创建环境并安装依赖：

```bash
# 进入项目目录（示例：cd ~/Desktop/DND）
cd 项目目录

# 创建虚拟环境（放在用户目录下，不随项目移动）
python3 -m venv ~/.local/share/project-venvs/DND/.venv

# 安装依赖（优先用锁定清单，保证依赖版本一致；Python 低于 3.12 时改用 requirements.txt）
~/.local/share/project-venvs/DND/.venv/bin/pip install -r requirements.lock.txt
```

之后每次启动只需：

```bash
cd 项目目录
~/.local/share/project-venvs/DND/.venv/bin/python -m streamlit run streamlit_app.py \
  --server.port 8502 --server.headless false \
  --server.showEmailPrompt false --browser.gatherUsageStats false
```

### Windows（命令提示符 CMD）

首次使用（虚拟环境不存在时）先创建环境并安装依赖：

```bat
rem 进入项目目录（示例：cd /d D:\DND）
cd /d 项目目录

rem 创建虚拟环境（放在用户目录下，不随项目移动）
py -3 -m venv %USERPROFILE%\.local\share\project-venvs\DND\.venv

rem 安装依赖（优先用锁定清单，保证依赖版本一致；Python 低于 3.12 时改用 requirements.txt）
%USERPROFILE%\.local\share\project-venvs\DND\.venv\Scripts\pip install -r requirements.lock.txt
```

之后每次启动只需：

```bat
cd /d 项目目录
%USERPROFILE%\.local\share\project-venvs\DND\.venv\Scripts\python -m streamlit run streamlit_app.py --server.port 8502 --server.headless false --server.showEmailPrompt false --browser.gatherUsageStats false
```

### 命令行方式说明

- 不需要手动设置数据目录：不设置 `VS_IMAGE_NOVEL_DATA_DIR` 环境变量时，程序会自动使用项目目录下的 `.python_app_data`。
- 如果 8502 端口被占用，把 `--server.port 8502` 改成其他端口（如 8503）即可。
- 启动后浏览器没有自动打开时，手动访问 `http://localhost:8502`。
- `--server.showEmailPrompt false` 用于跳过 Streamlit 首次运行的邮箱询问；`--browser.gatherUsageStats false` 关闭匿名使用统计。

## 数据与功能说明

API Key、Prompt、URL 收藏和聊天记录统一保存在自身目录的 `.python_app_data` 中。整个"普通模式独立版"文件夹移动后，这些本地数据会一起带走。虚拟环境不放在项目内（Mac/Linux 在 `~/.local/share/project-venvs/DND/`，Windows 在 `%USERPROFILE%\.local\share\project-venvs\DND\`），因此文件夹拷到别的电脑时会自动重建环境，不影响数据。

### 文本模型

普通聊天、Story Brain、世界书和角色卡生成均可选择 `Mistral Large 3 2512`，通过 OpenRouter 的 `mistralai/mistral-large-2512` 调用。它与 Seedance 视频模型共用 APIkey 页面中的 OpenRouter Key（或 Streamlit Secrets 中的 `OPENROUTER_API_KEY`）。当前仅接入文本聊天；示例中的图片输入格式未加入聊天界面。该模型没有可配置的思考模式，选择后相关思考控件会关闭。

### 图生视频

聊天相关请求（包括重发、自动掷骰、Story Brain 和图片/视频提示词）按队列顺序执行；图片生成和视频生成各有独立队列。三类队列可同时各运行一个任务，同类额外任务排队。需要先生成提示词或参考图的任务会等待对应结果，等待素材的任务不占用聊天执行位置；前置任务失败时不会继续调用依赖它的生成接口。

图片和视频完成后追加到当前聊天记录。图片生成完成后自动切换预览，直接显示新图片；视频保留完成提示和“查看”入口，点击后才切换预览。图片自动展示不会替换已经选定的视频参考图或手动修改的提示词。已明确选中的“待生成图片”完成后会衔接到同一张图片。

当前并发任务只在本次页面会话内管理，不支持浏览器刷新或应用重启后恢复任务。任务未完成时仍暂时禁止切换/新建对话和破坏性编辑。查询失败不会自动重新提交视频生成请求。

在“图生视频”区域选择模型后立即生效，时长下拉框位于输入图片下方。

- **Wan 2.2 I2V Fast**：使用 Replicate 的 `wan-video/wan-2.2-i2v-fast`，配置 `REPLICATE_API_TOKEN`；可选约 5、6、7、7.5 秒，实际为 81、97、113、121 帧 / 16 FPS，无音频。
- **Seedance 2.0 Mini**：使用 OpenRouter 的 `bytedance/seedance-2.0-mini`，支持 4–15 秒的整数时长。
- **Seedance 1.5 Pro**：使用 OpenRouter 的 `bytedance/seedance-1-5-pro`，支持 4–12 秒的整数时长。

两个 Seedance 共用 APIkey 页面的 OpenRouter Key（或 Streamlit Secrets 中的 `OPENROUTER_API_KEY`），默认 5 秒、720p、开启原生声音，画面比例按输入图片匹配。图生视频仍通过已有 Cloudinary 配置上传输入图片。

Seedance 点击“加载视频播放”后取回内存，画面与原生音轨通过同一播放器播放。点击“下载视频”才保存文件；应用不会自动保存视频到磁盘。历史记录保留服务商 URL，链接失效后无法重新加载，需要长期保留的视频请手动下载。旧视频记录不会因移除旧模型而删除。

新生成的图片默认只在聊天记录中保存服务商 URL，不自动写入 Base64。图片下方可选择“保存”将当前图片转为 Base64 长期保留，或选择“下载”保存为本地文件；未保存图片的 URL 明确失效后，会自动从媒体记录中移除。旧记录中已有的 Base64 图片继续兼容显示。

时长与音频能力依据 2026-09-10 的 [OpenRouter 模型列表](https://openrouter.ai/api/v1/videos/models) 和 [Replicate 参数](https://replicate.com/wan-video/wan-2.2-i2v-fast/api/schema)，集中配置于 `app/video_models.py`。当前未进行真实生成验证。

### 发布普通区 Prompt

提交 GitHub 前，先保存应用中的编辑，再运行：

```bash
python tools/export_public_prompt_assets.py
python tools/export_public_prompt_assets.py --check
```

导出文件为 `public_prompt_assets/ordinary.json`，包含普通区全部世界书、角色卡、固定模板、最终设定、长期记忆和快捷输入。导出不按名称筛选，保留本地当前正文、多个首次输入和普通区内的来源关联；新增、改名后重新导出即可。`--check` 会在导出过期时返回失败。支持 `--source` 指定设置文件，默认遵循 `VS_IMAGE_NOVEL_DATA_DIR`。

隐藏区、聊天记录及索引、密钥、媒体、URL 收藏不会进入导出文件。`.python_app_data/` 和 `.prompt_migration_backups/` 都被 Git 忽略，不要强制添加这些目录。最终设定若引用隐藏区或已不存在的来源，导出会提示并省略该关联，保留普通区最终正文，不修改本地关联。

新安装只在没有既有 Prompt 数据或历史初始化标记时导入公开资产；已有安装不覆盖、不自动合并、不复活已删除的条目。旧版 `naruto_public.json` 和按标题写死的初始化逻辑已移除。普通区内容修改后需要重新导出，单独 `git push` 不会读取本地运行数据。

`tools/migrate_prompt_spaces.py` 是本机这次分区调整的一次性工具，不参与应用启动或发布导出。默认只预览；`--apply` 才会备份整个数据目录并移动完整原记录。运行前必须停止本项目所有应用实例。它保留普通区的火影世界书、三张指定角色卡和火影最终设定，其余这三类记录追加到隐藏区；固定模板、长期记忆、快捷输入、聊天文件、索引和计数器不改。独立备份和迁移校验报告位于 `.prompt_migration_backups/`。

本版本只包含普通模式与短 Story Brain：

- 保留普通聊天、图片/视频生成、世界书、角色卡、固定模板、长期记忆规则、最终设定、URL 收藏和原有普通历史。
- 每个最终设定可绑定一份长期记忆规则，并保存多条可命名、可切换的首轮输入。
- Prompt 仅在有未保存的手动修改、且通过导航离开当前页面或切换 Prompt 分区时提示，可选择保存并离开、放弃修改并离开或继续编辑。普通操作、同页切换记录、自动保存字段、刷新和草稿恢复不弹窗；切回记录时静默恢复适用的草稿。
- Prompt 的“快捷输入”可保存名称和多行内容，底部支持保存、新建和删除。在聊天输入框任意位置输入 `/` 或 `／` 可打开候选列表，继续输入名称可筛选；点击或按方向键、Enter 选择后，只替换指令片段，保留前后文字，不直接发送。Esc 关闭列表，粘贴网址不会触发。
- 内置快捷输入不再提供“图片”；已有安装中未经修改的旧“图片”默认条目会清理，用户改写过的同名条目保留。手动输入 `/图片` 仍按普通聊天消息处理。普通账号仅可见普通快捷输入，私密账号可见全部，新建条目归属私密；编辑普通条目或续聊普通记录时保留原归属。
- “生成图片prompt”使用当前聊天的 System Prompt、上下文和短 Story Brain 生成可编辑草稿，但不会新增聊天轮次或进入后续上下文。图片 prompt 为空时直接点击“生成图片”，会先生成 prompt 再继续生图；已有内容时直接使用编辑框内容。
- 每张新图片严格绑定生成 prompt 时最新的 Assistant 轮次；之后生成视频 prompt 会按该轮次 ID 读取正文，因此编辑原回复会生效，原轮次撤回后会报错且不会回退到上一轮。
- 聊天、重发、图片 prompt、生图、视频 prompt 和生视频统一按触发顺序串行排队；前一项未完成时，后一项进入等待队列，不再因后台忙碌而丢失或直接报错。新建/切换记录、撤回和编辑等会改变状态基线的操作仍会在队列执行期间锁定。
- 图片生成 provider 新增 Replicate 社区模型 `anillustrious-v4`，固定使用版本 `80441e2c32a55f2fcf9b77fa0a74c6c86ad7deac51eed722b9faedb253265cb4`。默认 40 步、`DPM++ 2M SDE Karras`、开启 prompt conjunction；`refiner` 输入为空时关闭精修，填写后以 `refiner_prompt` 启用精修，强度为 0.4。三档尺寸分别为 768×1024、1024×768、1024×1024。该模型仅支持文生图，不接收参考图片 URL，并复用现有 `REPLICATE_API_TOKEN`。
- 图片生成 provider 同时提供 `wai-nsfw-illustrious-v12`，固定使用版本 `0fc0fa9885b284901a6f9c0b4d67701fd7647d157b88371427d63f8089ce140e`。请求固定为单图、40 步、`DPM++ 2M SDE Karras`、CFG 5.86、CLIP skip 2、随机 seed，并使用 `WAI-NSFW-Illustrious-SDXL-v12` 模型和 VAE。它复用相同的三档尺寸、Replicate Token 和异步轮询，也仅支持文生图。
- 所有图片 provider 在收到明确的非合规失败且没有图片时最多尝试 3 次（含首次）。内容合规拒绝会立即返回；提交连接中断时因无法确认远端是否已创建计费任务，也不会盲目重发。
- 下拉框中的“重发 · 操作”为固定指令，选择后只填入 `/重发`，再次发送才执行。整条消息为 `/重发` 或 `／重发` 时，用当前设置重发上一轮存储的用户内容，成功后替换原问答，失败保留原问答和记忆；后台任务执行期间不可重发。
- 每条聊天只保留当前版和上一版 Story Brain 快照（含更新进度），自动或手动更新后淘汰更早版本。撤回、重发跨过记忆更新边界时恢复可用快照；超出范围或旧记录没有快照时明确提示，保留当前记忆。快照随聊天保存并遵循同一账号归属，图片和视频不随重发删除。
- 不包含 Agent 模式、Agent Prompt 或 Agent 聊天记录。
- 不包含长 Story Brain 图谱、角色/关系/事件编辑器或 Memory Pack 调用。
- 旧聊天记录里的长 Story Brain 原始字段仅作为不可见兼容归档保留，不显示、不修改，也不会发送给模型。
- 从独立版开始产生的新记录和设置与原双模式软件互不同步。
