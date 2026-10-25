"""python3 -m nexsandglass_hermes install —— 见 install.py。

旧说明让人把整个 nexsandglass_hermes 目录拷进 $HERMES_HOME/plugins/nyx/；Hermes 加载目录插件时会把里面每个 .py
都当子模块执行一遍 —— 所以这里什么都不能在 import 时做。
"""
import sys

if __name__ == "__main__":
    if sys.argv[1:2] != ["install"]:
        print("用法：python3 -m nexsandglass_hermes install [--vault] [--vector] [--check] …（-h 看全部选项）")
        sys.exit(2)
    from nexsandglass_hermes.install import main
    sys.exit(main(sys.argv[2:]))
