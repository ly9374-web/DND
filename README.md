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

本版本只包含普通模式与短 Story Brain：

- 保留普通聊天、图片/视频生成、世界书、角色卡、固定模板、最终设定、URL 收藏和原有普通历史。
- 不包含 Agent 模式、Agent Prompt 或 Agent 聊天记录。
- 不包含长 Story Brain 图谱、角色/关系/事件编辑器或 Memory Pack 调用。
- 旧聊天记录里的长 Story Brain 原始字段仅作为不可见兼容归档保留，不显示、不修改，也不会发送给模型。
- 从独立版开始产生的新记录和设置与原双模式软件互不同步。
