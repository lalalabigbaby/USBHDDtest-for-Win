#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
移动硬盘稳定性测试脚本
======================
功能：
- 循环创建全1二进制测试文件，直到空间不足
- 主文件 256MB，剩余空间不足时自动降级为 128/64/32/16MB，尽量完全写满磁盘
- 对所有写入文件进行全1校验（快速 memcmp，无需 SHA256）
- 校验失败记录到日志但继续测试（不中断）
- 校验完成后删除所有临时文件
- 日志保存到其他磁盘（默认 C:/HDD_Test_Logs 或 D:/HDD_Test_Logs）
- 测试完成后自动复制日志到待测硬盘根目录
- 显示平均读写速度（MB/s）
- 日志记录每轮写入、校验、清理耗时
- 支持多实例锁防止并发冲突
- 异常保护：Ctrl+C 或错误时自动清理临时文件

使用方法：
  1. 将本脚本复制到待测移动硬盘根目录
  2. 在根目录下运行：python hdd_stability_test.py
  3. 输入循环次数（建议 1-10 轮）
  4. 测试过程中请勿拔出硬盘
"""

import os
import re
import sys
import time
import hashlib
import shutil
import datetime
import signal
import platform
import subprocess

# ================ 全局配置 ================
TEST_FILE_SIZE = 256 * 1024 * 1024           # 主测试文件大小 256MB
TEST_FILE_PREFIX = "hdd_test_"
TEST_FILE_EXT = ".bin"
LOCK_FILE_NAME = ".hdd_test.lock"
CHUNK_SIZE = 64 * 1024 * 1024               # 读写分块大小 64MB
ONES_CHUNK = bytes([255]) * CHUNK_SIZE  # 预生成全1块，减少重复分配
MIN_RESERVED_SPACE = 16 * 1024 * 1024      # 保留 16MB 给文件系统元数据

# 阶梯式文件大小（字节），按降序排列
TIERED_SIZES = [
    (256 * 1024 * 1024, "256MB"),
    (128 * 1024 * 1024, "128MB"),
    (64 * 1024 * 1024, "64MB"),
    (32 * 1024 * 1024, "32MB"),
    (16 * 1024 * 1024, "16MB"),
]

# 日志目录优先级（尽量选择与测试盘不同的盘符）
LOG_DIR_CANDIDATES = [
    r"C:\HDD_Test_Logs",
    r"D:\HDD_Test_Logs",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "HDD_Test_Logs"),
    os.path.join(os.environ.get("TEMP", ""), "HDD_Test_Logs"),
]

# ================ 全局变量 ================
script_dir = ""
lock_fd = None
lock_path = ""
log_path = ""
test_files = []           # 当前轮次的测试文件 [(filepath, expected_size), ...]
log_f_global = None       # 全局日志文件句柄

# 清理重试上限
MAX_CLEANUP_RETRIES = 3


def get_script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def get_drive_letter(path):
    drive = os.path.splitdrive(path)[0]
    return drive.upper().rstrip(":\\")


def format_size(size_bytes):
    if size_bytes is None:
        return " 未知"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"


def format_speed(bytes_per_sec):
    """格式化速度显示"""
    if bytes_per_sec <= 0:
        return "0 MB/s"
    mb = bytes_per_sec / (1024 * 1024)
    return f"{mb:.1f} MB/s"


def decide_file_size(free_space):
    """
    根据剩余空间决定本次写入的文件大小。
    阶梯式降级：256MB -> 128MB -> 64MB -> 32MB -> 16MB
    确保最终可以完全写满磁盘。
    返回 0 表示空间不足以写入最小文件。
    """
    usable = free_space - MIN_RESERVED_SPACE
    if usable <= 0:
        return 0
    for size, label in TIERED_SIZES:
        if usable >= size:
            return size
    return 0


def verify_all_ones(filepath, expected_size):
    """
    快速校验文件是否全为 0xFF。
    策略：
    1. 检查文件大小是否匹配
    2. 分块读取并逐块与全1字节串比较（C 层 memcmp，极快）
    返回 (is_valid, actual_size, error_msg)
    """
    try:
        actual_size = os.path.getsize(filepath)
        if actual_size != expected_size:
            return False, actual_size, f"大小不符：期望 {expected_size}，实际 {actual_size}"
    except Exception as e:
        return False, -1, f"获取大小失败：{e}"

    try:
        with open(filepath, 'rb') as f:
            offset = 0
            while offset < expected_size:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    return False, offset, "文件提前结束"
                # C 层 memcmp：chunk == b'xff' * len(chunk)
                if chunk != ONES_CHUNK[:len(chunk)]:
                    # 找到第一个非0xFF的位置
                    bad_pos = next((i for i, b in enumerate(chunk) if b != 0xFF), 0)
                    abs_bad = offset + bad_pos
                    byte_val = chunk[bad_pos] if bad_pos < len(chunk) else -1
                    return False, actual_size, f"偏移 {abs_bad} 处发现非0xFF字节：0x{byte_val:02X}"
                offset += len(chunk)
        return True, actual_size, ""
    except Exception as e:
        return False, -1, f"读取失败：{e}"


def write_test_file(filepath, file_size):
    """
    写入指定大小的全1 (0xFF) 测试文件。
    使用预生成的全1块，避免随机数生成开销。
    返回写入的字节数。
    """
    written = 0
    with open(filepath, 'wb') as f:
        while written < file_size:
            chunk_size = min(CHUNK_SIZE, file_size - written)
            f.write(ONES_CHUNK[:chunk_size])
            written += chunk_size
            os.fsync(f.fileno())  # 确保数据落盘，测试真实写入
    return written


def get_disk_free(path):
    try:
        return shutil.disk_usage(path).free
    except Exception:
        return 0


def acquire_lock(lock_path):
    """
    获取文件锁，防止多实例同时运行。
    1. 尝试用 O_EXCL 原子创建锁文件
    2. 若已存在，读取 PID 并检查进程是否存活
    3. 若进程不存在，强制清除旧锁并重试
    返回锁文件描述符，失败返回 None
    """
    global lock_fd
    max_retries = 3
    for attempt in range(max_retries):
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_EXCL)
            pid_bytes = str(os.getpid()).encode('utf-8')
            os.write(lock_fd, pid_bytes)
            os.fsync(lock_fd)
            return lock_fd
        except FileExistsError:
            try:
                with open(lock_path, 'r') as f:
                    old_pid = int(f.read().strip())
                if is_process_alive(old_pid):
                    # 有有效实例在运行
                    return None
                else:
                    # 旧实例已退出，强制清除旧锁
                    try:
                        os.remove(lock_path)
                    except Exception:
                        pass
                    continue
            except Exception:
                try:
                    os.remove(lock_path)
                except Exception:
                    pass
                continue
        except OSError as e:
            if attempt == max_retries - 1:
                print(f"无法创建锁文件：{e}")
                return None
            time.sleep(0.1)
    return None


def is_process_alive(pid):
    """检查进程是否存活（跨平台）"""
    try:
        if platform.system() == 'Windows':
            result = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                capture_output=True, text=True, timeout=5
            )
            # tasklist 输出形如 "python.exe 1234 Console ..."，用正则匹配独立 PID 列，
            # 避免 1234 误匹配到 12345 等相近 PID（多实例同时运行时尤其重要）
            return re.search(rf'(?m)^\S+\s+{pid}\s+', result.stdout) is not None
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def release_lock(lock_fd, lock_path):
    """释放文件锁并删除锁文件（仅当锁文件仍属于本进程，避免误删其他实例的锁）"""
    try:
        if lock_fd is not None:
            try:
                if platform.system() == 'Windows':
                    try:
                        import msvcrt
                        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                    except (ImportError, IOError, OSError):
                        pass
                os.close(lock_fd)
            except Exception:
                pass
        if os.path.exists(lock_path):
            try:
                with open(lock_path, 'r') as f:
                    lock_pid = f.read().strip()
                # 仅当锁文件记录的是本进程 PID 时才删除
                if lock_pid == str(os.getpid()):
                    os.remove(lock_path)
            except Exception:
                pass
    except Exception:
        pass


def find_log_dir():
    """寻找可用的日志目录（优先其他磁盘）"""
    script_drive = get_drive_letter(script_dir)
    # 探测文件名带 PID，避免多实例同时探测时互相删除对方的探测文件
    probe_name = f".permission_test_{os.getpid()}"
    for candidate in LOG_DIR_CANDIDATES:
        try:
            test_file = os.path.join(candidate, probe_name)
            os.makedirs(candidate, exist_ok=True)
            with open(test_file, 'w') as f:
                f.write("test")
            os.remove(test_file)
            candidate_drive = get_drive_letter(candidate)
            if candidate_drive and candidate_drive != script_drive:
                return candidate
        except Exception:
            continue
    # 回退：如果所有其他盘都不可用，使用脚本所在盘
    fallback = os.path.join(script_dir, "test_logs")
    try:
        os.makedirs(fallback, exist_ok=True)
        return fallback
    except Exception:
        return script_dir


def log_message(log_f, msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] {msg}"
    print(line)
    log_f.write(line + "\n")
    log_f.flush()


def cleanup_test_files():
    """
    清理当前轮次的所有临时测试文件。
    增加单文件重试，提升 FAT/NTFS 可移动盘删除成功率。
    返回 (删除成功数, 失败数, 删除前空间, 删除后空间, 清理耗时秒)
    """
    global test_files
    free_before = get_disk_free(script_dir)
    deleted = 0
    failed = 0
    t0 = time.time()

    pending = list(test_files)
    for attempt in range(MAX_CLEANUP_RETRIES):
        still_pending = []
        for filepath, _ in pending:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    deleted += 1
            except Exception as e:
                if attempt < MAX_CLEANUP_RETRIES - 1:
                    # 仍有机会重试
                    still_pending.append((filepath, _))
                else:
                    failed += 1
                    if log_f_global:
                        log_message(log_f_global, f"  删除失败 {os.path.basename(filepath)}：{e}")
                    else:
                        print(f"  删除失败 {os.path.basename(filepath)}：{e}")
        pending = still_pending
        if not pending:
            break
        time.sleep(0.5)

    elapsed = time.time() - t0
    free_after = get_disk_free(script_dir)
    test_files = []
    return deleted, failed, free_before, free_after, elapsed


def run_test(cycles, auto_confirm=False):
    global test_files, log_f_global, lock_fd, lock_path, log_path, script_dir

    log_dir = find_log_dir()
    drive_letter = get_drive_letter(script_dir)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"HDD_Stability_{drive_letter}_{timestamp}.log"
    log_path = os.path.join(log_dir, log_filename)

    print(f"\n{'='*60}")
    print(f"      移动硬盘稳定性测试启动")
    print(f"{'='*60}")
    print(f"测试盘符：{drive_letter}")
    print(f"测试目录：{script_dir}")
    print(f"日志路径：{log_path}")
    print(f"阶梯文件大小：256MB / 128MB / 64MB / 32MB / 16MB")
    print(f"保留空间：{format_size(MIN_RESERVED_SPACE)}")
    print(f"循环次数：{cycles}")
    print(f"{'='*60}\n")

    lock_fd = acquire_lock(lock_path)
    if lock_fd is None:
        print(f"错误：检测到其他实例正在运行。")
        print(f"锁文件：{lock_path}")
        print("请先关闭其他实例，或结束对应进程后重试。")
        input("按回车键退出...")
        sys.exit(1)

    try:
        with open(log_path, 'w', encoding='utf-8') as log_f:
            log_f_global = log_f
            log_message(log_f, "=" * 60)
            log_message(log_f, "移动硬盘稳定性测试启动")
            log_message(log_f, f"测试盘符：{drive_letter}")
            log_message(log_f, f"测试目录：{script_dir}")
            log_message(log_f, f"日志路径：{log_path}")
            log_message(log_f, f"阶梯文件大小：256MB/128MB/64MB/32MB/16MB")
            log_message(log_f, f"保留空间：{format_size(MIN_RESERVED_SPACE)}")
            log_message(log_f, f"循环次数：{cycles}")
            log_message(log_f, f"系统：{platform.system()} {platform.release()}")
            log_message(log_f, f"Python：{platform.python_version()}")
            log_message(log_f, "=" * 60)

            total_write_time = 0
            total_verify_time = 0
            total_cleanup_time = 0
            total_files_written = 0
            total_bytes_written = 0
            total_errors = 0

            for cycle in range(1, cycles + 1):
                log_message(log_f, f"\n{'='*60}")
                log_message(log_f, f">>> 开始第 {cycle}/{cycles} 轮测试")
                log_message(log_f, f"{'='*60}")

                written_files = []  # [(filepath, file_size), ...]
                file_index = 0

                # ---------- 阶段1：写入 ----------
                log_message(log_f, "阶段1：写入测试文件（全0xFF，阶梯降级直到写满）")
                write_start = time.time()
                while True:
                    free_space = get_disk_free(script_dir)
                    log_message(log_f, f"  当前剩余空间：{format_size(free_space)}")

                    file_size = decide_file_size(free_space)
                    if file_size == 0:
                        log_message(log_f, f"  剩余空间 {format_size(free_space)} 不足以写入最小文件"
                                         f"（{format_size(MIN_RESERVED_SPACE)}），停止写入。")
                        break

                    size_label = next(label for s, label in TIERED_SIZES if s == file_size)
                    filename = f"{TEST_FILE_PREFIX}{file_index:06d}{TEST_FILE_EXT}"
                    filepath = os.path.join(script_dir, filename)

                    try:
                        write_test_file(filepath, file_size)
                        written_files.append((filepath, file_size))
                        test_files.append((filepath, file_size))
                        log_message(log_f, f"  ✓ [{file_index:06d}] {filename} | "
                                         f"写入 {size_label}")
                    except Exception as e:
                        log_message(log_f, f"  ✗ [{file_index:06d}] {filename} 写入失败：{e}")
                        # 如果空间确实不足，自然退出
                        if free_space < MIN_RESERVED_SPACE + file_size:
                            break

                    file_index += 1

                write_elapsed = time.time() - write_start
                total_write_time += write_elapsed
                total_files_written += len(written_files)
                cycle_bytes = sum(f[1] for f in written_files)  # 本轮写入字节数
                total_bytes_written += cycle_bytes

                # 计算写入速度（用本轮字节数，避免累计量/单轮耗时导致虚高）
                avg_write_speed = cycle_bytes / write_elapsed if write_elapsed > 0 else 0

                log_message(log_f, f"写入阶段结束：共 {len(written_files)} 个文件，"
                                 f"总耗时 {write_elapsed:.2f}s，"
                                 f"平均速度 {format_speed(avg_write_speed)}")

                # ---------- 阶段2：校验 ----------
                error_count = 0
                pass_count = 0
                verified_bytes = 0  # 本轮实际校验字节数（按文件真实大小累加）
                verify_start = time.time()

                if written_files:
                    log_message(log_f, "阶段2：校验文件完整性（全0xFF快速校验）")
                    for idx, (filepath, expected_size) in enumerate(written_files):
                        filename = os.path.basename(filepath)
                        try:
                            is_valid, actual_size, err_msg = verify_all_ones(filepath, expected_size)
                            verified_bytes += expected_size  # 按该文件真实大小累加
                            if is_valid:
                                pass_count += 1
                                if idx < 5 or idx == len(written_files) - 1:
                                    log_message(log_f, f"  ✓ {filename} 校验通过")
                                elif idx == 5:
                                    log_message(log_f, f"  ... (共 {len(written_files)} 个文件，"
                                                     f"仅显示首尾) ...")
                            else:
                                error_count += 1
                                log_message(log_f, f"  ✗ {filename} 校验失败！{err_msg}")
                        except Exception as e:
                            error_count += 1
                            log_message(log_f, f"  ✗ {filename} 校验异常：{e}")

                    verify_elapsed = time.time() - verify_start
                    total_verify_time += verify_elapsed
                    total_errors += error_count

                    # 校验速度按实际校验字节数（阶梯文件不是固定 256MB）
                    avg_verify_speed = verified_bytes / verify_elapsed if verify_elapsed > 0 else 0

                    log_message(log_f, f"校验完成：通过 {pass_count}，失败 {error_count}，"
                                     f"总计 {len(written_files)}")
                    log_message(log_f, f"校验阶段耗时：{verify_elapsed:.2f}s，"
                                     f"平均速度 {format_speed(avg_verify_speed)}")
                    if error_count > 0:
                        log_message(log_f, "警告：检测到数据损坏或写入错误！请检查硬盘健康状态。")
                else:
                    log_message(log_f, "本轮写入文件数为0，跳过校验。")

                # ---------- 阶段3：清理 ----------
                log_message(log_f, "阶段3：删除临时测试文件")
                deleted, failed, free_before, free_after, cleanup_elapsed = cleanup_test_files()
                total_cleanup_time += cleanup_elapsed
                log_message(log_f, f"清理完成：已删除 {deleted}/{len(written_files)} 个文件，"
                                 f"耗时 {cleanup_elapsed:.2f}s")
                log_message(log_f, f"磁盘空间：清理前 {format_size(free_before)} -> "
                                 f"清理后 {format_size(free_after)}，"
                                 f"释放 {format_size(free_after - free_before)}")
                if failed:
                    log_message(log_f, f"  警告：{failed} 个文件删除失败，可能影响下一轮写入。")

                # 轮次总结
                free_space = get_disk_free(script_dir)
                log_message(log_f, f"第 {cycle} 轮结束：写入 {write_elapsed:.2f}s | "
                                 f"校验 {verify_elapsed:.2f}s | 清理 {cleanup_elapsed:.2f}s | "
                                 f"剩余 {format_size(free_space)}")

                if cycle < cycles:
                    log_message(log_f, f"即将开始第 {cycle+1} 轮...\n")

            # ---------- 最终总结 ----------
            total_test_time = total_write_time + total_verify_time + total_cleanup_time
            log_message(log_f, "\n" + "=" * 60)
            log_message(log_f, "所有测试完成！")
            log_message(log_f, "=" * 60)
            log_message(log_f, f"测试盘符：{drive_letter}")
            log_message(log_f, f"总轮次数：{cycles}")
            log_message(log_f, f"总测试文件数：{total_files_written}")
            log_message(log_f, f"总写入字节数：{format_size(total_bytes_written)}")
            log_message(log_f, f"总校验失败数：{total_errors}")
            log_message(log_f, f"总写入耗时：{total_write_time:.2f}s")
            log_message(log_f, f"总校验耗时：{total_verify_time:.2f}s")
            log_message(log_f, f"总清理耗时：{total_cleanup_time:.2f}s")
            log_message(log_f, f"总测试耗时：{total_test_time:.2f}s")
            if total_files_written > 0 and total_test_time > 0:
                overall_speed = total_bytes_written / total_test_time
                log_message(log_f, f"整体平均速度：{format_speed(overall_speed)}")
            log_message(log_f, f"日志文件：{log_path}")
            log_message(log_f, "=" * 60)

    finally:
        # 无论正常完成还是异常中断，都清理临时文件（正常路径 test_files 已为空，幂等）。
        # 防止异常退出时测试文件残留导致磁盘写满、影响后续测试。
        log_f_global = None  # 日志上下文可能已关闭，清理阶段仅输出到控制台
        try:
            if test_files:
                cleanup_test_files()
        except Exception as e:
            print(f"清理临时文件时出错：{e}")
        release_lock(lock_fd, lock_path)

    # 测试完成后复制日志到待测硬盘根目录
    try:
        log_dest = os.path.join(script_dir, os.path.basename(log_path))
        shutil.copy2(log_path, log_dest)
        print(f"日志已复制到待测盘：{log_dest}")
    except Exception as e:
        print(f"复制日志到待测盘失败：{e}")

    print(f"\n测试完成！")
    print(f"详细日志请查看：{log_path}")
    if not auto_confirm:
        input("按回车键退出...")


def signal_handler(sig, frame):
    """处理 Ctrl+C，确保清理"""
    print("\n\n检测到中断信号，正在清理...")
    global log_f_global
    try:
        if log_f_global:
            log_message(log_f_global, "测试被用户中断（Ctrl+C）")
        if test_files:
            log_message(log_f_global, "正在清理临时文件...")
            cleanup_test_files()
    except Exception:
        pass
    release_lock(lock_fd, lock_path)
    print("清理完成，退出。")
    sys.exit(130)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="移动硬盘稳定性测试工具")
    parser.add_argument("-c", "--cycles", type=int, default=0,
                        help="测试轮次（1-9999）；缺省为交互式输入")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="自动确认，跳过交互确认与结尾回车等待")
    args = parser.parse_args()

    global script_dir, lock_path
    script_dir = get_script_dir()
    lock_path = os.path.join(script_dir, LOCK_FILE_NAME)

    # 注册信号处理
    if platform.system() == 'Windows':
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    else:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    print("=" * 60)
    print("        移动硬盘稳定性测试工具")
    print("=" * 60)
    print(f"脚本位置：{script_dir}")
    print(f"日志目录：{LOG_DIR_CANDIDATES[0]} (自动选择可用目录)")
    print()

    if 0 < args.cycles <= 9999:
        cycles = args.cycles
        print(f"已设定测试循环次数：{cycles} 轮")
    else:
        while True:
            try:
                cycles_str = input("请输入测试循环次数（正整数，1-9999）：").strip()
                if not cycles_str.isdigit():
                    print("请输入有效的数字。")
                    continue
                cycles = int(cycles_str)
                if cycles <= 0 or cycles > 9999:
                    print("请输入 1 到 9999 之间的数字。")
                    continue
                break
            except ValueError:
                print("输入无效，请重新输入。")

    print(f"\n即将开始 {cycles} 轮测试...")
    print(f"主文件大小：{format_size(TEST_FILE_SIZE)}（空间不足时自动降级：128/64/32/16MB）")
    print("每轮流程：写满磁盘 -> 全0xFF校验 -> 删除文件 -> 下一轮")
    print("日志保存到其他磁盘，即使待测盘损坏也不会丢失。")
    print("测试完成后日志会自动复制到待测盘根目录。\n")

    if args.yes:
        confirm = 'y'
    else:
        confirm = input("确认开始测试？(y/N)：").strip().lower()
    if confirm != 'y':
        print("已取消。")
        return

    run_test(cycles, auto_confirm=args.yes)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        signal_handler(None, None)
    except Exception as e:
        print(f"\n发生未预期的错误：{e}")
        import traceback
        traceback.print_exc()
        input("按回车键退出...")
        sys.exit(1)