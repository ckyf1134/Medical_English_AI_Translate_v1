# 离线运行与杀毒说明

> **这个文件夹是自包含的。** `v1.0` 里带着自己的 `runtime\`、`models\`、`vendor\`，
> 复制到任何 Windows 10/11 x64 电脑都能直接跑，不需要项目根目录，也不需要 v2.0。

## 已确认的误报

火绒曾把项目自动生成的 `start_ollama.ps1` 报为：

`HEUR:TrojanDownloader/PS.NetLoader.ae`

原因不是模型或 Ollama 本体，而是脚本同时包含：

- 访问本地网络地址；
- 根据结果启动新的后台进程；
- 自动化环境变量和运行参数。

这类组合会触发“NetLoader”行为启发式。该脚本已废弃，不再使用。

## 安全启动方式

项目不会自动启动 Ollama，也不会自动下载任何文件。

如果需要使用项目自带的 Ollama，请在普通 PowerShell 窗口中手动执行：

```powershell
cd <v1.0 所在路径>
$env:OLLAMA_MODELS = "$PWD\models\ollama"
$env:OLLAMA_LIBRARY_PATH = "$PWD\runtime\ollama\lib\ollama"
$env:OLLAMA_HOST = "127.0.0.1:11434"
$env:OLLAMA_MAX_LOADED_MODELS = "1"
$env:OLLAMA_NUM_PARALLEL = "1"
& "$PWD\runtime\ollama\ollama.exe" serve
```

（`cd` 到 v1.0 实际所在的路径，例如 `D:\Agent\v1.0`。）

这条命令只是运行经过 Ollama Inc. 数字签名的 `ollama.exe`，不包含下载器逻辑。

如果电脑上已经安装 Ollama，也可以直接运行已安装的 Ollama，然后把模型目录指向本文件夹中的：

```text
<v1.0 所在路径>\models\ollama
```

## 运行 Agent

先保持 Ollama 运行，再打开另一个 PowerShell 窗口：

```powershell
cd <v1.0 所在路径>
.\runtime\python\python.exe .\webui\app.py
```

或者双击 `start.bat`。

## 复制到其他电脑

**只复制 `v1.0` 这一个文件夹就够了**，里面已经包含：

- Python 运行环境（`runtime\python\`，含 pymupdf，自带 VC++ 运行库）；
- Ollama 运行文件（`runtime\ollama\`）；
- Qwen3-14B-Q4KM、Gemma-4-E4B、DeepSeek-R1-1.5B 的本地模型仓库（`models\ollama\`，约 15GB）；
- 项目代码、提示词、术语表和质检规则。

复制完成后，首次运行前手动执行上面的 Ollama 启动命令。整个过程不需要联网。

### 目标电脑的要求

| 事项 | 说明 |
|---|---|
| 系统 | Windows 10 / 11 x64 |
| 显卡 | 有 NVIDIA 显卡和驱动 → 正常速度；没有也能跑，但 14B 模型走 CPU 会慢 20 倍以上 |
| OCR | 用系统自带的 `Windows.Media.Ocr`，需要系统安装了对应语言包（英文通常自带；中文简体需要在“设置 → 时间和语言 → 语言”里添加） |
| 磁盘 | v1.0 约 18GB |

