"""让 tests/ 变成一个可以被 Python 导入的包。

文件是空的，但必须有。作用只有一个：
    python -m unittest discover -s tests -t .
这条命令要靠它把 tests 当成包来扫描。

不要删。删了会发现「0 个测试被找到」，但不会报错 —— 这种静默失败最难查。
"""
