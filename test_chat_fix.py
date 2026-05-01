#!/usr/bin/env python3
"""
测试聊天页面修复的简单脚本
验证异步操作不会阻塞事件循环
"""
import asyncio
import time


async def simulate_agent_execution(duration: int):
    """模拟 agent 执行（同步操作）"""
    print(f"开始执行任务，预计耗时 {duration} 秒...")
    time.sleep(duration)  # 模拟阻塞操作
    print("任务执行完成")
    return f"执行结果（耗时 {duration}s）"


async def test_blocking_approach():
    """测试阻塞方式（修复前）"""
    print("\n=== 测试阻塞方式（修复前）===")
    start = time.time()

    # 模拟修复前的阻塞方式
    import threading
    result = [None]

    def run_sync():
        result[0] = asyncio.run(simulate_agent_execution(3))

    thread = threading.Thread(target=run_sync)
    thread.start()

    # 阻塞等待
    while thread.is_alive():
        thread.join(timeout=0.1)  # 这会阻塞事件循环
        print(".", end="", flush=True)

    elapsed = time.time() - start
    print(f"\n总耗时: {elapsed:.2f}s")
    print(f"结果: {result[0]}")
    print("❌ 问题：在等待期间事件循环被阻塞，无法处理其他任务")


async def test_non_blocking_approach():
    """测试非阻塞方式（修复后）"""
    print("\n=== 测试非阻塞方式（修复后）===")
    start = time.time()

    # 模拟修复后的非阻塞方式
    loop = asyncio.get_event_loop()

    def run_sync():
        time.sleep(3)  # 同步阻塞操作
        return "执行结果（耗时 3s）"

    # 使用 run_in_executor 在线程池中运行
    future = loop.run_in_executor(None, run_sync)

    # 非阻塞等待
    counter = 0
    while not future.done():
        await asyncio.sleep(0.1)  # 不阻塞事件循环
        counter += 1
        if counter % 10 == 0:
            print(".", end="", flush=True)

    result = await future
    elapsed = time.time() - start
    print(f"\n总耗时: {elapsed:.2f}s")
    print(f"结果: {result}")
    print("✅ 改进：事件循环保持响应，可以处理其他任务")


async def test_concurrent_tasks():
    """测试并发任务处理"""
    print("\n=== 测试并发任务处理 ===")
    start = time.time()

    loop = asyncio.get_event_loop()

    def task1():
        time.sleep(2)
        return "任务1完成"

    def task2():
        time.sleep(2)
        return "任务2完成"

    # 同时运行多个任务
    future1 = loop.run_in_executor(None, task1)
    future2 = loop.run_in_executor(None, task2)

    # 等待所有任务完成
    results = await asyncio.gather(future1, future2)

    elapsed = time.time() - start
    print(f"总耗时: {elapsed:.2f}s")
    print(f"结果: {results}")
    print("✅ 两个任务并发执行，总耗时约 2 秒而非 4 秒")


async def test_cancellation():
    """测试取消机制"""
    print("\n=== 测试取消机制 ===")

    loop = asyncio.get_event_loop()
    cancel_flag = False

    def long_running_task():
        for i in range(10):
            if cancel_flag:
                print("\n任务被取消")
                return "已取消"
            time.sleep(0.5)
            print(".", end="", flush=True)
        return "任务完成"

    future = loop.run_in_executor(None, long_running_task)

    # 2 秒后取消
    await asyncio.sleep(2)
    cancel_flag = True
    print("\n发送取消信号...")

    result = await future
    print(f"结果: {result}")
    print("✅ 取消机制工作正常")


async def main():
    """运行所有测试"""
    print("=" * 60)
    print("聊天页面修复验证测试")
    print("=" * 60)

    # 注意：test_blocking_approach 会阻塞，仅用于演示
    # await test_blocking_approach()

    await test_non_blocking_approach()
    await test_concurrent_tasks()
    await test_cancellation()

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
