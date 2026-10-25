"""启动器：不管你在哪个目录、不管编辑器怎么点，跑这个文件都能起来。

为什么需要它：
    `python -m agent_playground` 只有在「项目根目录」（agent-playground/）里
    执行才成立。一旦当前目录变成包内部（agent-playground/agent_playground/），
    Python 就会去 agent_playground/agent_playground/ 里找一个叫 agent_playground
    的包，找不到，于是报：
        No module named agent_playground

    这个文件直接写死了「项目根在哪」，所以不受当前目录影响。

用法（三种都行）：
    python run.py
    python E:/workshuju/2026-10-23-17-52-58/agent-playground/run.py
    双击 run.bat
"""

import sys
from pathlib import Path

# __file__ 是这个 run.py 自己的路径，.parent 就是它所在的目录，
# 也就是项目根 agent-playground/。resolve() 把它转成绝对路径。
ROOT = Path(__file__).resolve().parent

# sys.path 是 Python 找模块时依次搜索的目录列表。
# 把项目根塞到最前面，import agent_playground 才能成功。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 这行 import 必须放在上面之后，否则还是找不到包。
from agent_playground.cli import main  # noqa: E402

# main() 返回退出码，raise SystemExit 把它交给操作系统（0 表示正常结束）。
# 和 __main__.py 里那句写法一样。
raise SystemExit(main())
