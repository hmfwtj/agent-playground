"""让 python -m agent_playground 能直接启动。

Python 执行一个包时，找的就是这个文件。有它才能省掉 .cli 那一截。
"""

import sys
from pathlib import Path

# ★ 为什么要有下面这几行：
#
# 正常跑法是 `python -m agent_playground`，此时 Python 把「包的上一层目录」
# 放进搜索路径，所以 import agent_playground 能找到包自己。
#
# 但如果直接在编辑器里点「运行」这个文件，Python 会把**本文件所在目录**
# （也就是 agent_playground/ 里面）当成搜索起点。于是 import agent_playground
# 就去找 agent_playground/agent_playground/，当然找不到，报：
#     ModuleNotFoundError: No module named 'agent_playground'
#
# 下面两行把上一层目录补进搜索路径，让两种跑法都能用。
# 正规做法仍然是 `python -m agent_playground`，这里只是让点运行不报错。
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 这个 import 必须放在上面两行之后，否则照样找不到包。
# 编辑器可能提示「import 不在文件顶部」，这里是有意为之。
from agent_playground.cli import main  # noqa: E402

raise SystemExit(main())
