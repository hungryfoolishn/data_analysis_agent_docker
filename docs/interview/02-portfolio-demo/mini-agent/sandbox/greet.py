"""演示用的小模块：Agent 将在这个文件上执行任务。"""


def greet(name: str) -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("World"))
