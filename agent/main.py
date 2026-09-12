"""
AgentServer 子进程入口: 供通用 GUI (MFAAvalonia/MFW) 分发模式使用。

GUI 通过 interface.json 的 agent 字段拉起本进程, 本进程向框架注册
win_click / ff_active_reco (实现与 runner.py 共用同一份代码)。

开发调试时不需要本进程 —— runner.py 在进程内注册了同样的实现。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maa.agent.agent_server import AgentServer
from maa.toolkit import Toolkit

from runner import FfAvailable, WinClick


def main():
    Toolkit.init_option("./debug")

    if len(sys.argv) < 2:
        print("Usage: python agent/main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)
    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    AgentServer.register_custom_action("win_click", WinClick())
    AgentServer.register_custom_recognition("ff_active_reco", FfAvailable())
    print("agent 就绪: win_click / ff_active_reco 已注册")
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
