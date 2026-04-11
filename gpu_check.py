import torch
import sys

print("=" * 60)
print("  PyTorch GPU 检测报告")
print("=" * 60)

# 1. PyTorch 版本信息
print(f"\n  Python 版本:    {sys.version.split()[0]}")
print(f"  PyTorch 版本:   {torch.__version__}")
print(f"  CUDA 编译版本:  {torch.version.cuda or '无 (CPU版本)'}")
print(f"  cuDNN 版本:     {torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else '不可用'}")

# 2. GPU 可用性
cuda_available = torch.cuda.is_available()
print(f"\n  CUDA 可用:      {'是' if cuda_available else '否'}")
print(f"  GPU 数量:       {torch.cuda.device_count()}")

if cuda_available:
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        print(f"\n  --- GPU {i} ---")
        print(f"  名称:           {props.name}")
        print(f"  显存:           {props.total_memory / 1024**3:.1f} GB")
        print(f"  计算能力:       {props.major}.{props.minor}")
        print(f"  多处理器数量:   {props.multi_processor_count}")

    # 3. 简单的 GPU 计算测试
    print(f"\n{'=' * 60}")1
    print("  GPU 计算测试")
    print("=" * 60)
    
    device = torch.device("cuda")
    
    # 在 GPU 上创建张量并做矩阵乘法
    a = torch.randn(1000, 1000, device=device)
    b = torch.randn(1000, 1000, device=device)
    
    # 预热
    for _ in range(3):
        c = torch.matmul(a, b)
    torch.cuda.synchronize()
    
    # 计时
    import time
    start = time.time()
    for _ in range(100):
        c = torch.matmul(a, b)
    torch.cuda.synchronize()
    gpu_time = time.time() - start

    # CPU 对比
    a_cpu = a.cpu()
    b_cpu = b.cpu()
    start = time.time()
    for _ in range(100):
        c_cpu = torch.matmul(a_cpu, b_cpu)
    cpu_time = time.time() - start

    print(f"\n  GPU 矩阵乘法 (1000x1000, 100次): {gpu_time:.4f} 秒")
    print(f"  CPU 矩阵乘法 (1000x1000, 100次): {cpu_time:.4f} 秒")
    print(f"  GPU 加速倍数: {cpu_time / gpu_time:.1f}x")
    
    # 显存使用情况
    print(f"\n  当前显存使用:   {torch.cuda.memory_allocated() / 1024**2:.1f} MB")
    print(f"  最大显存使用:   {torch.cuda.max_memory_allocated() / 1024**2:.1f} MB")

    print(f"\n  [结果] GPU 工作正常!")
else:
    print(f"\n  [结果] GPU 不可用，PyTorch 正在使用 CPU 模式。")
    print("  可能原因:")
    print("    1. 安装的是 CPU 版本的 PyTorch")
    print("    2. NVIDIA 驱动未安装或版本不兼容")
    print("    3. 系统没有 NVIDIA GPU")

print("\n" + "=" * 60)
