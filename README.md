<div align="center">

# MaaInf

**In Falsus 剧情跳过自动化助手 · 基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework)**

剧情段自动推进 · 音游段完全静默 · 解压即用

</div>

---

## 功能特性

- **剧情段全自动**：对话推进、快进加速、剧情树选读最新章节，全程无需操作
- **音游段零干扰**：卡牌装配、演奏、结算等阶段只识别、不注入任何输入，把操作完整交还给玩家
- **多分辨率自适应**：16:9 / 16:10 任意分辨率与窗口模式，画面内容区自动归一化识别
- **图形界面**：启动/停止按钮、全局热键、实时日志面板
- **无缝接管**：音游结算后直接进入的剧情段也能立即识别并继续跳过

## 使用方法

1. 从 [Releases](../../releases) 下载便携包 zip，解压到任意目录
2. 启动 Steam 版 In Falsus，停在剧情树界面
3. 双击 **MaaInf.exe**，点击【启动】或按 **F9**
4. 需要终止时按 **F10** 或点击【停止】，随时可再次启动

| 热键 | 功能 |
| --- | --- |
| F9 | 启动（全局生效，游戏内可按） |
| F10 | 停止（全局生效） |

### 注意事项

- 首次运行 Windows SmartScreen 会拦截一次（exe 未做数字签名），点「更多信息 → 仍要运行」；个别杀毒软件可能误报，加入白名单即可
- `MaaInf.exe` 必须与压缩包内其他文件（`python\`、`assets\`、`runner.py`）保持同目录，不要单独拷贝 exe
- 目前按**游戏中文界面**标定识别关键词；HDR 显示器建议关闭后再使用
- 建议使用窗口或无边框全屏模式运行游戏

## 工作原理

通过 Unity 窗口类名绑定游戏窗口 → Windows.Graphics.Capture 截屏 → 模板匹配 / OCR / 自定义识别判定当前界面状态 → 自定义 SendInput 动作完成点击；判定为音游相关状态时只轮询不操作。分辨率自适应采用短边归一化，内容区恒定映射为 1280×720，模板与 ROI 全分辨率通用。

## 开发

```bash
pip install -r requirements.txt
python -u runner.py          # 控制台运行
python gui_shell.py          # 图形界面（开发模式）
```

界面壳 `gui_shell.py` 与运行逻辑 `runner.py` 分离：壳进程不加载 maa 原生库（规避 PyInstaller 打包冲突），以子进程方式拉起运行器并回显日志。开发文档见 [docs/zh_cn/develop](./docs/zh_cn/develop/)。

## 免责声明

本项目为个人效率辅助工具，仅供学习交流使用，与游戏官方及发行方无关。请在遵守游戏用户协议的前提下自行评估使用风险；因使用本项目产生的一切后果由使用者自行承担。

## 鸣谢

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework) — 图像识别自动化框架
- [MaaPracticeBoilerplate](https://github.com/MaaXYZ/MaaPracticeBoilerplate) — 项目模板
