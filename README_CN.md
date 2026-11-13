# @epiphanye/pi-hud · Rana 桌面宠物

让 Pi Coding Agent 的状态以常驻桌面的 **Rana 桌宠**呈现：透明角色动画、状态气泡、桌面置顶与可拖动位置。它是标准 Pi Package：TypeScript 扩展负责接收 Pi 生命周期事件；Python / PySide6 客户端负责显示原生桌宠。

## 安装

先安装 PySide6：

```powershell
# Windows / WSL2：安装到 Windows 原生 Python
py -3 -m pip install PySide6
```

```bash
# Linux 桌面
python3 -m pip install --user PySide6
```

安装已发布版本：

```bash
pi install npm:@epiphanye/pi-hud
```

本地使用此工程：

```bash
pi install /home/epiphanye/dev/floating-status-hud
```

重启 Pi 或执行 `/reload` 后生效。请删除旧的手工扩展，避免争用 HUD 端口：

```bash
rm -f ~/.pi/agent/extensions/hud.ts ~/.pi/agent/extensions/hud_client_windows.py
```

## 平台

| Pi 运行环境 | 桌宠窗口 |
| --- | --- |
| WSL2 Ubuntu | Windows 原生 Python / Win32 Topmost |
| Windows | Windows 原生 Python |
| Linux 桌面 | Linux Python / PySide6 |

在 WSL2 中即使启用 WSLg，也优先创建 Windows 原生窗口，以覆盖普通 Windows 应用。

## 状态与动作

Rana 使用随包提供的精灵图动画。每种状态会持续循环，直到 Pi 事件或命令改变状态。

| Pi 状态 | Rana 动作行 | 气泡 |
| --- | --- | --- |
| 空闲 | 第 1 或第 7 行（进入时随机，之后保持该循环） | 默认一直显示 |
| 思考与执行中 | 第 9 行 | 默认一直显示 |
| 目标达成 | 第 4 行 | 默认一直显示，随后 Pi 自动回到空闲 |
| 执行出错 | 第 6 行 | 默认一直显示 |
| 等待用户确认 | 第 8 行 | 默认一直显示 |
| 向右拖动 | 第 2 行 | 默认一直显示 |
| 向左拖动 | 第 3 行 | 默认一直显示 |
| 上下拖动 | 第 5 行 | 默认一直显示 |

精灵图按固定 **8×9** 格子切帧（1536×1872，单格 192×208）。帧数不足 8 的行只播放左侧有效帧，不按整行宽度重新均分。空闲、完成和错误动作约为 **3 FPS**；工作、等待确认和拖动动作约为 **4 FPS**。

默认角色高度约为 **160px**。气泡根据当前状态文字自适应宽度并位于角色正上方；超长文字会省略。气泡与角色一起常驻并等比缩放，只有右键“隐藏气泡”才会关闭。桌宠位置、气泡偏好和大小保存在：

```text
~/.pi/hud-pet.json
```

旧版 `~/.pi/hud-position.json` 的位置会在首次运行时迁移。

## 命令

```text
/hud             # 显示 / 隐藏桌宠
/hud show        # 显示桌宠
/hud hide        # 隐藏桌宠
/hud restart     # 重启 HUD 服务与桌宠
/hud waiting     # 显式进入“等待用户确认”状态
/hud idle        # 立即回到空闲状态
/hud status      # 显示端口、心跳、素材和当前动画诊断
```

等待确认不通过猜测 Agent 文本触发：当需要时使用 `/hud waiting`；确认完成后使用 `/hud idle`，或继续让 Pi 执行下一轮任务。

## 操作

- **左键拖动**：角色可见轮廓和气泡可拖动，外围透明区域会将点击传递给下层窗口。向右、向左、上下分别播放第 2、3、5 行；同方向移动连续播放，中途改变方向才切换动作。轻微点击不会开始拖动，松开后记住位置并恢复当前 Pi 状态动作。
- **Ctrl + 滚轮**：按约 16px 步进缩放角色高度（80–320px）；气泡、文字和精确点击热区一起缩放。右键“大小”可选小 / 默认 / 大 / 重置。
- **左键双击（Windows / WSL）**：恢复启动桌宠前处于前台的 Edge、Windows Terminal 或 VS Code 宿主窗口；若窗口最小化会先恢复。
- **右键菜单**：显示/隐藏气泡、查看当前状态与动画帧、调整大小、重新置顶、隐藏桌宠或退出桌宠。
- **退出桌宠**只关闭图形窗口，不停止 Pi 服务；健康检查会尝试重新拉起客户端，也可使用 `/hud restart`。
- `/hud restart` 会先等待旧连接、TCP 端口和 Windows 单实例锁释放，再重新拉起客户端，因此可能有约 1 秒的短暂延迟；可用 `/hud status` 查看 `phase`、端口、最近启动和具体错误。

> Windows 只能稳定恢复 Edge、VS Code 或终端的顶层窗口，不能可靠定位 `http://127.0.0.1:30141/` 的特定 Edge 标签页，也不能选择具体 Terminal / VS Code 终端标签。

## 生命周期与限制

- Pi CLI、Pi Web **服务**、会话切换或 `/reload` 正常结束时，桌宠会立即关闭。
- 关闭单个 Pi Web 浏览器页面不等于停止 Pi Web 后端服务，因此桌宠会保留到服务停止。
- Pi 被强制终止、WSL 被关闭或本地通信中断时，桌宠约 **12 秒**收不到有效服务端消息后自动退出。
- Windows 使用单实例互斥锁，重复启动不会叠加多个桌宠。
- 桌宠会定期重新申请 Windows Topmost；无边框窗口化和窗口化全屏游戏通常可恢复显示。

独占全屏游戏、UAC 安全桌面、锁屏、受保护内容和不同管理员权限级别由 Windows 管理，普通桌面程序无法保证置顶或激活窗口。

## 诊断

在 Pi 中执行：

```text
/hud status
```

输出会包含重启阶段、端口绑定、最近启动与错误、客户端心跳、素材状态，以及当前 clip、精灵图行/帧、实际 FPS、气泡和缩放诊断。

WSL 中可验证 Windows Python 启动器：

```bash
/mnt/c/Windows/pyw.exe -3 --version
```

若启动器不在默认位置，在启动 Pi 前设置：

```bash
export PI_HUD_PYW="/mnt/c/Windows/pyw.exe"
pi
```
